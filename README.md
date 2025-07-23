# Simple Static File Server

This project provides a simple and elegant web server for serving static files. It uses Caddy as the web server and a Python backend to dynamically generate checksums for the files. The frontend is built with HTML and htmx for a lightweight, dynamic user experience.

## Features

*   **Static File Serving**: Serves files from a `static` directory.
*   **Dynamic Checksums**: Automatically calculates and displays MD5 and SHA256 checksums for each file.
*   **Real-time Updates**: The file list updates automatically when files are added or removed from the `static` directory.
*   **Elegant Frontend**: A clean and simple user interface.

## Production Deployment

This guide provides instructions for deploying the application in a production environment.

### Prerequisites

*   **A server**: A Linux server (e.g., Ubuntu, Debian) with a public IP address.
*   **A domain name**: A domain name pointing to your server's IP address.
*   **Caddy**: The Caddy web server. You can find installation instructions at [caddyserver.com](https://caddyserver.com/docs/install).
*   **Python**: Python 3.6 or higher.
*   **Git**: For cloning the repository.

### 1. Installation

First, clone the repository to your server:

```bash
git clone <your-repository-url>
cd <your-repository-directory>
```

Next, create a Python virtual environment and install the required dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration

#### Caddy

The `Caddyfile` is configured to work out-of-the-box for local development. For a production deployment, you'll need to make a few changes.

Replace the contents of the `Caddyfile` with the following, replacing `your-domain.com` with your actual domain name:

```
your-domain.com {
    # Caddy will automatically provision and renew a TLS certificate for your domain.
    # It will also redirect HTTP to HTTPS.

    # Serve static files
    @static {
        file {
            root /path/to/your/project/static
            try_files {path}
        }
    }

    route {
        file_server @static {
            root /path/to/your/project/static
        }
        # Proxy all other requests to the Python backend
        reverse_proxy localhost:5000
    }
}
```

**Note**: Make sure to replace `/path/to/your/project` with the actual absolute path to the project directory on your server.

#### Python Backend

The Python application (`app/main.py`) is configured to run on `localhost:5000`. This is the recommended setup for production, as Caddy will be acting as a reverse proxy and handling all incoming traffic.

### 3. Running the Application

For a production environment, it's important to run the Python backend as a service to ensure it's always running. Here's an example of how to do this using `systemd`.

#### Create a systemd Service File

Create a new file at `/etc/systemd/system/melissa.service` with the following content:

```ini
[Unit]
Description=Melissa Static File Server
After=network.target

[Service]
User=your-user
Group=your-group
WorkingDirectory=/path/to/your/project
ExecStart=/path/to/your/project/.venv/bin/python app/main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

**Note**:
*   Replace `your-user` and `your-group` with the user and group you want to run the application as.
*   Replace `/path/to/your/project` with the actual absolute path to the project directory.

#### Enable and Start the Service

Now, enable and start the service:

```bash
sudo systemctl enable melissa.service
sudo systemctl start melissa.service
```

You can check the status of the service with:

```bash
sudo systemctl status melissa.service
```

### 4. Start Caddy

Finally, start Caddy. If you installed it as a service, you can use `systemd`:

```bash
sudo systemctl start caddy
```

If you're running it manually, navigate to the directory containing your `Caddyfile` and run:

```bash
caddy run
```

Your website should now be live at `https://your-domain.com`.
