This is a backend service that provides an API that serves the api-gateway's calls. It is responsible for handling the business logic and interacting with the database.

## Requirements
- The service should be able to handle requests from the api-gateway and return appropriate responses.
- The service should be serverless and deployed on Cloudflare Workers.
- The service should be able to interact with a R2 Storage to retrieve and store data as needed.
- The service should be scheduled to clearn up old compressed files from R2 Storage to manage storage space effectively.

## Technical Stack
- Python for the backend logic.
- Cloudflare Workers for deployment.
- R2 Storage for data storage.
- Take the scheduled miniutes to clean up old compressed files from R2 Storage. It will be provide through environment variable.

## API Endpoints
- `POST /compress`: Accepts Object Key then retrieved the stored file from R2 Stroage, compresses it and then stores the compressed file back to R2 Storage. Finally, it returns the pre-signedURL from R2 Storage of the compressed file.
= `POST /merge`: Accepts multiple objectKeys, retrieves the stored files from R2 Storage, merges them into a single file, stores the merged file back to R2 Storage, and returns the pre-signedURL from R2 Storage of the merged file.

# Notes
- The compressed file name should be orignal file name with a suffix "-compressed" before the file extension. For example, if the original file is "image.jpg", the compressed file should be named "image-compressed.jpg".
- The service should handle errors gracefully and return appropriate error messages in case of failures.
- All necessary environment variables and configurations should be properly set up for the service to function correctly. Such as R2 Storage credentials, Cloudflare Workers configurations, and Allowed origins for CORS.
- The service should be designed to be scalable and efficient, ensuring that it can handle a high volume of requests without performance degradation.
- Proper logging and monitoring should be implemented to track the performance and identify any issues that may arise during the operation of the service.
- The merged file name should be first file name with a suffix "-merged" before the file extension. For example, if the original files are "file1.txt" and "file2.txt", the merged file should be named "file1-merged.txt".

