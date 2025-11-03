# Deployment Instructions

This document provides instructions on how to deploy the Nutrition Assistant application.

## Prerequisites

- Ubuntu 22.04 or later
- Python 3.10 or later
- Nginx
- A Google Gemini API Key

## Deployment Steps

1.  **Clone the repository:**

    ```bash
    git clone <repository-url>
    cd <repository-directory>
    ```

2.  **Run the deployment script:**

    The `deploy.sh` script will set up a virtual environment, install the necessary dependencies, configure the application to run as a service with Gunicorn and Nginx, and create a `.env` file with the necessary API keys.

    You can run the script with your Gemini API key as an argument:

    ```bash
    ./deploy.sh YOUR_GEMINI_API_KEY
    ```

    Alternatively, you can set the `GEMINI_API_KEY` environment variable:

    ```bash
    GEMINI_API_KEY=YOUR_GEMINI_API_KEY ./deploy.sh
    ```

3.  **Verify the deployment:**

    The deployment script will automatically start the application and Nginx. You can verify that the application is running by checking the status of the `nutri` service:

    ```bash
    sudo systemctl status nutri
    ```

    You can also use `curl` to send a request to the `/health` endpoint:

    ```bash
    curl -s http://localhost/health
    ```

    If the deployment was successful, you should see an "OK" response.

## Troubleshooting

If you encounter any issues during the deployment, you can check the following logs for more information:

-   **Application logs:**

    ```bash
    sudo journalctl -u nutri -n 200 --no-pager
    ```

-   **Nginx error logs:**

    ```bash
    sudo tail -n 200 /var/log/nginx/error.log
    ```

If the `nutri.sock` file is not created, you can try running the Gunicorn command directly to get more detailed logs:

```bash
/tmp/venv/bin/gunicorn --chdir /app --workers 3 --bind unix:/app/nutri.sock --timeout 120 app:app --log-level debug
```
