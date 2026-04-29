# Markd - Website Feedback Annotation Tool

Collect feedback on any website by allowing visitors to highlight text and leave comments.

## Architecture

- **API Server** (`server_api.py`): Handles sessions, annotations, and serves the main landing page
- **Lambda Proxy** (`lambda_proxy.py`): Fetches target websites, removes CORS restrictions, and injects annotation overlay
- **Local Lambda Server** (`lambda_server.py`): Development server for the lambda function

## Quick Start

1. **Run both servers:**
   ```bash
   uv run run_dev.py
   ```

   This automatically installs dependencies and starts both servers.

   This starts:
   - API server on http://localhost:8000
   - Lambda proxy on http://localhost:9000

3. **Use the tool:**
   - Go to http://localhost:8000
   - Enter any website URL
   - Share the generated link
   - Visitors can highlight text and comment!

## Manual Testing

You can test the lambda function directly:

```bash
# Test the lambda function
uv run lambda_proxy.py https://example.com test123 http://localhost:8000

# Test with the lambda server
curl "http://localhost:9000?url=https://example.com&session=test123&api=http://localhost:8000"
```

## Files

- `server_api.py` - Main API server (no proxy logic)
- `lambda_proxy.py` - Lambda function for CORS removal and script injection
- `lambda_server.py` - Local development server for lambda function
- `run_dev.py` - Development setup script
- `server.py` - Original monolithic server (deprecated)

## Deployment

For production:

1. Deploy `server_api.py` to your preferred platform (Railway, Heroku, etc.)
2. Deploy `lambda_proxy.py` to AWS Lambda
3. Update the `LAMBDA_URL` environment variable in the API server

## Environment Variables

- `DB_PATH` - SQLite database path (default: "markd.db")
- `LAMBDA_URL` - Lambda function URL (default: "http://localhost:9000")
