
# Start app in local
```
wrangler dev --remote --port 8787
```

# Deploy to Cloudflare Pages
Keep all variables as secret type
```
> Deploy command:npx wrangler deploy --keep-vars
> Put Non-production branch deployment command as : npx wrangler versions upload
```

# Install on Koyeb
```
> builder: Buildpacks
> build command: leave empty
> run command: leave empty (use Procfile)
> Work directory: leave empty
```
# Run
```
python -m uvicorn main:app --host 0.0.0.0 --port 8787
```