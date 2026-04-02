# This app is
provide backend service for api-gateway, which is used to manage agents and their tasks.
Typical flow is take the Cloudflare R2 Storage objectId then process the file and return the result(presignedUrl) to api-gateway, then api-gateway will send the result to the agent.

## Tech stack
- Python 3.8+ : The programming language used for developing the backend service.
- FastAPI : A modern, fast (high-performance) web framework for building APIs with Python 3.6+ based on standard Python type hints.
- pymupdf : A Python binding for MuPDF, a lightweight PDF and XPS viewer. It is used for processing PDF documents.
- Cloudflare R2 Storage : A cloud storage service used for storing and retrieving PDF documents.

## API Endpoints
- `POST /convert`: Convert a PDF document.
- `POST /compress`: Compress a PDF document.
- `POST /merge`: Merge multiple PDF documents.
- `GET //hello`: A simple endpoint to wake up the service.
- `POST /remove-background`: Remove the background from an image stored in R2. Request body: `{"objectKey": "<r2-key>"}`. Returns a presigned URL to the processed PNG.
	Uses the `rembg` library when available for accurate foreground extraction; falls back to a simpler heuristic if `rembg` is not installed.

## Testing
All new code **MUST** be accompanied by appropriate tests to ensure functionality and prevent regressions. Test code must be placed in the `tests/` directory and follow the naming convention `test_*.py`. Tests should cover various scenarios, including edge cases, to ensure robustness.
- Unit Tests: Use `pytest` to write unit tests for individual functions and components of the backend service.

## Session Management Protocol
To maintain state across context, the Agent **MUST ALWAYS** follow these steps:
1. **Start of Session:** Read `docs/PROGRESS.md` to identify the current session state and progress.
2. **Task Completion:** Upon completing a task, update 'PROGRESS.md' with the new state and progress details.
- What was changed/fixed.
- Any new technical debt or "gotchas" discovered
3. **End of Session:** When the session is complete, update 'PROGRESS.md' to reflect the final state and any remaining tasks or issues.

---
** Reference `docs/VALIDATION.md` for testing requirements. **