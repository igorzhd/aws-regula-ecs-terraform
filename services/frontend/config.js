// ============================================================
// config.js — Frontend Configuration
// ============================================================
// Set API_BASE to your CloudFront domain (e.g. https://d1234abcd.cloudfront.net)
// or your custom domain if Route 53 is configured (e.g. https://regula-ecs-lab.example.com).
// Terraform outputs this value as `cloudfront_domain_name` after apply.
window.APP_CONFIG = {
  API_BASE: 'https://your-cloudfront-domain.cloudfront.net'
};
