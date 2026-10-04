"""The authentik button: nexcrate sets up its own OpenID Connect provider and application in authentik.

The owner hands over the address of authentik and a one-time API token. nexcrate then does through the authentik API v3
what one would otherwise click together in a dozen forms: a signing key, the provider, the application, a binding that
lets only the token's own user into the application, and finally its own settings. Every step reports whether it
succeeded and, if not, what went wrong. The first failure stops the run; what came before stays, and running the button
again updates instead of duplicating.

The binding matters: an authentik application without one is open to every user of that authentik. nexcrate refuses
every identity but the linked one anyway (``sign_in``), but authentik then turns the others away before they reach it.

The token is used for these calls only. It is never stored and never logged; the answer to the owner names HTTP status
and content type of a failure, never the token and never the body (the address could point at any service).

The blueprint is the way for owners who would rather not hand over a token: a YAML file for authentik's blueprint import
that creates the same provider and application, with the default self-signed certificate as signing key because a
blueprint cannot generate one, and without the binding because a blueprint does not know whose nexcrate this is.

Written against the authentik API conventions of 2024.x to 2026.x (list endpoints with ``results``, ``redirect_uris`` as
a list of objects, ``invalidation_flow`` required on providers). Since authentik 2026.8 a provider carries
``grant_types``, and the authorize endpoint refuses every request whose grant is not listed there with
``invalid_request``; a provider created through the API without the field gets an empty list. So the button names the
one grant nexcrate uses. Older versions ignore the field.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy.orm import Session as OrmSession

from . import oidc, sign_in

logger = logging.getLogger("nexcrate.authentik")

NAME = "nexcrate"
SLUG = "nexcrate"
#: authentik's own mappings for the scopes nexcrate asks for, found by their managed name. nexcrate needs no address:
#: it links by subject, so the email scope stays out.
MANAGED_MAPPINGS = (
    "goauthentik.io/providers/oauth2/scope-openid",
    "goauthentik.io/providers/oauth2/scope-profile",
)
#: nexcrate runs the authorization code flow and nothing else: no refresh tokens, no client credentials, no devices.
GRANT_TYPES = ("authorization_code",)
PREFERRED_AUTHORIZATION_FLOW = "default-provider-authorization-implicit-consent"
PREFERRED_INVALIDATION_FLOW = "default-provider-invalidation-flow"
CERT_VALIDITY_DAYS = 3650
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
#: How much of an error answer goes into the log. authentik's error pages are long.
DETAIL_MAX = 300

STEP_KEYS = ("reached", "owner", "signing_key", "mappings", "provider", "application", "binding", "filled")

#: Tests set an ``httpx.MockTransport`` here.
transport_for_tests: httpx.BaseTransport | None = None


class StepFailed(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass
class Step:
    key: str
    ok: bool
    detail: str = ""


@dataclass
class SetupResult:
    steps: list[Step] = field(default_factory=list)
    issuer: str = ""
    #: The authentik user the application was bound to: the owner of the token.
    owner: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(step.ok for step in self.steps) and len(self.steps) == len(STEP_KEYS)


class _Api:
    """The few calls nexcrate needs, with the token in the Authorization header and nowhere else."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=TIMEOUT,
            transport=transport_for_tests,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, path: str, *, params: dict[str, str] | None = None, body: Any = None) -> Any:
        url = f"{self.base_url}/api/v3{path}"
        try:
            response = await self._client.request(method, url, params=params, json=body)
        except httpx.HTTPError as error:
            kind = error.__class__.__name__
            raise StepFailed(f"{method} {path}: authentik at {self.base_url} is not reachable ({kind})") from error
        except Exception as error:
            # ``httpx.InvalidURL`` is not an HTTPError; the address comes from the owner.
            kind = error.__class__.__name__
            raise StepFailed(f"{method} {path}: the address {self.base_url!r} cannot be used ({kind})") from error
        if not response.is_success:
            text = response.text.strip().replace("\n", " ")[:DETAIL_MAX]
            logger.debug("authentik %s %s answered %s: %r", method, path, response.status_code, text)
            kind = response.headers.get("content-type", "").split(";")[0].strip() or "no content type"
            hint = " (is the token valid and allowed to do this?)" if response.status_code in (401, 403) else ""
            raise StepFailed(f"{method} {path} answered {response.status_code} ({kind}){hint}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise StepFailed(f"{method} {path} answered {response.status_code} without JSON") from error

    async def find_one(self, path: str, params: dict[str, str], key: str, value: str) -> dict[str, Any] | None:
        """The one list entry whose ``key`` equals ``value``. The filter narrows the list; the exact comparison here
        makes sure a looser filter or an ignored parameter does not hand back a stranger."""
        data = await self.call("GET", path, params=params)
        results = data.get("results", []) if isinstance(data, dict) else []
        for entry in results:
            if isinstance(entry, dict) and str(entry.get(key) or "") == value:
                return entry
        return None


def _pk(entry: dict[str, Any], what: str) -> Any:
    pk = entry.get("pk")
    if pk in (None, ""):
        raise StepFailed(f"{what} has no pk in authentik's answer")
    return pk


async def _reached(api: _Api) -> str:
    data = await api.call("GET", "/admin/version/")
    version = str((data or {}).get("version_current") or "") if isinstance(data, dict) else ""
    return f"authentik {version}" if version else "authentik reached, version unknown"


async def _owner(api: _Api) -> tuple[Any, str]:
    """The user the token belongs to: the only one the application will let in."""
    data = await api.call("GET", "/core/users/me/")
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        raise StepFailed("authentik's answer names no user for this token")
    username = str(user.get("username") or "")
    return _pk(user, "the token's user"), username


async def _signing_key(api: _Api) -> tuple[Any, str]:
    existing = await api.find_one("/crypto/certificatekeypairs/", {"name": NAME}, "name", NAME)
    if existing is not None:
        return _pk(existing, "certificate"), f"using the existing certificate {NAME!r}"
    created = await api.call(
        "POST",
        "/crypto/certificatekeypairs/generate/",
        body={"common_name": NAME, "subject_alt_name": "", "validity_days": CERT_VALIDITY_DAYS, "alg": "rsa"},
    )
    if not isinstance(created, dict):
        raise StepFailed("generating the certificate answered without an object")
    return _pk(created, "certificate"), f"generated the certificate {NAME!r} ({CERT_VALIDITY_DAYS} days)"


async def _mappings(api: _Api) -> tuple[list[Any], str]:
    path = "/propertymappings/provider/scope/"
    ids = []
    for managed in MANAGED_MAPPINGS:
        default = await api.find_one(path, {"managed": managed}, "managed", managed)
        if default is None:
            raise StepFailed(f"authentik's scope mapping {managed!r} was not found")
        ids.append(_pk(default, "scope mapping"))
    return ids, "found the scope mappings for openid and profile"


async def _flow(api: _Api, designation: str, preferred: str) -> Any:
    data = await api.call("GET", "/flows/instances/", params={"designation": designation})
    entries = data.get("results", []) if isinstance(data, dict) else []
    results = [entry for entry in entries if isinstance(entry, dict)]
    if not results:
        raise StepFailed(f"no flow with designation {designation!r} in authentik")
    for entry in results:
        if entry.get("slug") == preferred:
            return _pk(entry, "flow")
    return _pk(results[0], "flow")


async def _provider(api: _Api, redirect_uri: str, signing_key: Any, mappings: list[Any]) -> tuple[Any, str, str, str]:
    authorization = await _flow(api, "authorization", PREFERRED_AUTHORIZATION_FLOW)
    invalidation = await _flow(api, "invalidation", PREFERRED_INVALIDATION_FLOW)
    body = {
        "name": NAME,
        "authorization_flow": authorization,
        "invalidation_flow": invalidation,
        "client_type": "confidential",
        "grant_types": list(GRANT_TYPES),
        "redirect_uris": [{"matching_mode": "strict", "url": redirect_uri}],
        "signing_key": signing_key,
        "sub_mode": "user_uuid",
        "property_mappings": mappings,
        "include_claims_in_id_token": True,
    }
    existing = await api.find_one("/providers/oauth2/", {"name": NAME}, "name", NAME)
    if existing is None:
        answer = await api.call("POST", "/providers/oauth2/", body=body)
        note = f"created the provider {NAME!r}"
    else:
        answer = await api.call("PATCH", f"/providers/oauth2/{_pk(existing, 'provider')}/", body=body)
        note = f"updated the existing provider {NAME!r}"
    if not isinstance(answer, dict):
        raise StepFailed("the provider call answered without an object")
    client_id = str(answer.get("client_id") or "")
    client_secret = str(answer.get("client_secret") or "")
    if not client_id or not client_secret:
        raise StepFailed("authentik's provider answer carries no client_id or client_secret")
    return _pk(answer, "provider"), client_id, client_secret, note


async def _application(api: _Api, provider_pk: Any) -> tuple[Any, str]:
    body = {"name": NAME, "slug": SLUG, "provider": provider_pk}
    existing = await api.find_one("/core/applications/", {"slug": SLUG}, "slug", SLUG)
    if existing is None:
        answer = await api.call("POST", "/core/applications/", body=body)
        note = f"created the application {SLUG!r}"
    else:
        answer = await api.call("PATCH", f"/core/applications/{SLUG}/", body=body)
        note = f"updated the existing application {SLUG!r}"
    if not isinstance(answer, dict):
        raise StepFailed("the application call answered without an object")
    return _pk(answer, "application"), note


async def _binding(api: _Api, application_pk: Any, user_pk: Any, username: str) -> str:
    """Bind the token's user to the application. With a binding in place, authentik lets in only whoever passes one."""
    data = await api.call("GET", "/policies/bindings/", params={"target": str(application_pk)})
    entries = data.get("results", []) if isinstance(data, dict) else []
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get("user") or "") == str(user_pk):
            return f"the application already lets {username!r} in"
    await api.call(
        "POST",
        "/policies/bindings/",
        body={"target": application_pk, "user": user_pk, "order": 0, "enabled": True, "negate": False, "timeout": 30},
    )
    return f"the application lets only {username!r} in"


def issuer_for(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/application/o/{SLUG}/"


async def setup(db: OrmSession, base_url: str, token: str, redirect_uri: str) -> SetupResult:
    """The whole run, step by step. Stops at the first failure; the result lists every step that ran."""
    result = SetupResult(issuer=issuer_for(base_url))
    api = _Api(base_url, token)
    user_pk: Any = None
    signing_key: Any = None
    mappings: list[Any] = []
    provider_pk: Any = None
    application_pk: Any = None
    client_id = client_secret = ""
    try:
        for key in STEP_KEYS:
            try:
                if key == "reached":
                    detail = await _reached(api)
                elif key == "owner":
                    user_pk, result.owner = await _owner(api)
                    detail = f"the token belongs to {result.owner!r}"
                elif key == "signing_key":
                    signing_key, detail = await _signing_key(api)
                elif key == "mappings":
                    mappings, detail = await _mappings(api)
                elif key == "provider":
                    provider_pk, client_id, client_secret, detail = await _provider(
                        api, redirect_uri, signing_key, mappings
                    )
                elif key == "application":
                    application_pk, detail = await _application(api, provider_pk)
                elif key == "binding":
                    detail = await _binding(api, application_pk, user_pk, result.owner)
                else:
                    detail = await _fill(db, result.issuer, client_id, client_secret)
            except StepFailed as error:
                result.steps.append(Step(key, False, error.detail))
                logger.warning("authentik setup stopped at step %s: %s", key, error.detail)
                break
            result.steps.append(Step(key, True, detail))
            logger.info("authentik setup step %s done: %s", key, detail)
    finally:
        await api.close()
    return result


async def _fill(db: OrmSession, issuer: str, client_id: str, client_secret: str) -> str:
    """Store the settings, then confirm the issuer with one discovery call.

    Stored first: the values are what authentik handed out, and a failed discovery usually means nexcrate cannot reach
    authentik under this address, which the owner fixes at the network or with a corrected address.
    """
    sign_in.save_provider(db, issuer, client_id, client_secret, "authentik")
    db.commit()
    try:
        await oidc.discovery(issuer, fresh=True)
    except oidc.OidcError as error:
        raise StepFailed(f"settings stored, but the discovery at {issuer} failed: {error.code}") from error
    return f"settings stored, discovery at {issuer} confirmed"


# --- The blueprint ------------------------------------------------------------------------------------------------ #


def blueprint(redirect_uri: str) -> str:
    """A blueprint (schema version 1) that creates the same provider and application as the button.

    ``!Find`` and ``!KeyOf`` are authentik's YAML tags; the file is assembled as text so they come out as tags, not as
    quoted strings. The redirect URI is emitted as a JSON string, which is a valid double-quoted YAML scalar whatever
    characters it carries.
    """
    redirect = json.dumps(redirect_uri)
    return f"""# nexcrate: OpenID Connect provider and application for authentik.
#
# Apply it under Customization, Blueprints (create a blueprint from this file) or drop it into the
# blueprints/custom/ directory of the authentik worker. Afterwards copy client id and client secret from the
# provider "nexcrate" (Applications, Providers) into nexcrate under Settings, System, Account, and enter the issuer
# <authentik address>/application/o/nexcrate/.
#
# Bind your own user to the application "nexcrate" (Applications, nexcrate, Policy / Group / User Bindings):
# without a binding the application is open to every user of this authentik. nexcrate lets in only the one
# identity you link, but authentik should not send the others to it at all.
#
# The signing key is authentik's default self-signed certificate: a blueprint cannot generate one. Pick a
# different certificate at the provider afterwards if you prefer.
#
# Written against the authentik blueprint schema v1 (2024.x to 2026.x). grant_types exists since authentik 2026.8,
# where a provider without it refuses every sign-in; older versions ignore the line.
version: 1
metadata:
  name: {NAME}
  labels:
    blueprints.goauthentik.io/description: OpenID Connect provider and application for nexcrate
entries:
  - model: authentik_providers_oauth2.oauth2provider
    state: present
    id: nexcrate-provider
    identifiers:
      name: {NAME}
    attrs:
      authorization_flow: !Find [authentik_flows.flow, [slug, {PREFERRED_AUTHORIZATION_FLOW}]]
      invalidation_flow: !Find [authentik_flows.flow, [slug, {PREFERRED_INVALIDATION_FLOW}]]
      client_type: confidential
      grant_types:
        - {GRANT_TYPES[0]}
      redirect_uris:
        - matching_mode: strict
          url: {redirect}
      signing_key: !Find [authentik_crypto.certificatekeypair, [name, authentik Self-signed Certificate]]
      sub_mode: user_uuid
      include_claims_in_id_token: true
      property_mappings:
        - !Find [authentik_providers_oauth2.scopemapping, [managed, {MANAGED_MAPPINGS[0]}]]
        - !Find [authentik_providers_oauth2.scopemapping, [managed, {MANAGED_MAPPINGS[1]}]]
  - model: authentik_core.application
    state: present
    identifiers:
      slug: {SLUG}
    attrs:
      name: {NAME}
      provider: !KeyOf nexcrate-provider
"""
