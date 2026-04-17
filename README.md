
# Start app in local
```
python -m uvicorn main:app --host 0.0.0.0 --port 8787
```

# Deploy to Cloudflare Pages
Keep all variables as secret type
```
> Deploy command:npx wrangler deploy --keep-vars
> Put Non-production branch deployment command as : npx wrangler versions upload
```

# Install on Koyeb
```
> build command: leave empty
> run command: uvicorn main:app --host 0.0.0.0 --port $PORT
> Work directory: leave empty
```

# Debug
```
// - Go to Run & Debug → create launch.json
// - Choose Python
// - Add this config:

{
  "name": "FastAPI (uvicorn)",
  "type": "python",
  "request": "launch",
  "module": "uvicorn",
  "args": [
    "main:app",
    "--host", "0.0.0.0",
    "--port", "8787"
  ],
  "jinja": true
}
```