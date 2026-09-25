/*
 * Starts Swagger UI.
 *
 * A file of its own, not an inline <script>: the Content-Security-Policy allows scripts
 * from 'self' only. An inline start script would need a hash that changes with every edit.
 */

window.ui = SwaggerUIBundle({
  // Relative on purpose: resolves against /api/docs to /api/openapi.json.
  url: 'openapi.json',
  dom_id: '#swagger-ui',
  layout: 'BaseLayout',
  deepLinking: true,
  // The validator badge would load an image from a foreign host, which the CSP refuses.
  validatorUrl: null,
  presets: [SwaggerUIBundle.presets.apis],
  // Requests that change something need this header, otherwise nexcrate answers
  // 403 csrf_header_missing. The session cookie travels by itself (same origin).
  requestInterceptor: (request) => {
    request.headers['X-Requested-With'] = 'nexcrate'
    return request
  },
})
