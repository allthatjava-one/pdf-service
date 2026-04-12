I want to make a new end point /pdf-splitter that will allow users to upload a PDF file and split it into individual pages. Each page will be saved as a separate PDF file and made available for download. The endpoint will accept a POST request with the PDF file as form data, and it will return a JSON response containing the URLs of the split PDF files.

# Technologies Used
- Python
- pyMupdf

# Implementation Steps
1. Set up a new endpoint `/split` in the web application.
2. Create a function to handle the POST request and process the uploaded PDF file.
3. Parameter will have objectKey that contained in R2 storage.
4. Use the pyMupdf library to read the PDF file and split it into individual pages.
5. There will be parameter "splitOption" that contains page number or page range such as "1-3" or "5". Or even "1,3,5-7"
6. Save each split page as a separate PDF file in the R2 storage.
7. Generate URLs for the split PDF files and return them in a JSON response.

# Error Handling
- Validate the uploaded file to ensure it is a PDF.
- Handle cases where the PDF file is corrupted or cannot be processed.
- Return appropriate error messages for invalid input or processing errors.

