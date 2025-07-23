# Simple Static File Server

This project provides a simple and elegant web server for serving static files. It uses Caddy as the web server and a Python backend to dynamically generate checksums for the files. The frontend is built with HTML and htmx for a lightweight, dynamic user experience.

## Features

*   **Static File Serving**: Serves files from a `static` directory with recursive subdirectory support.
*   **Dynamic Checksums**: Automatically calculates and displays MD5 checksums for each file.
*   **Real-time Updates**: The file list updates automatically when files are added, removed, or modified in the `static` directory using Server-Sent Events (SSE).
*   **Collapsible Directory Tree**: Files are organized in an expandable tree structure with directories collapsed by default for easy navigation.
*   **Directory Descriptions**: Add `description.txt` files to directories to provide contextual information that appears when directories are expanded.
*   **Human-readable File Sizes**: File sizes are displayed in KB, MB, GB format instead of raw bytes.
*   **Customizable Branding**: Dynamic configuration through the `config/` directory for logos, organization name, headings, and footer links.
*   **Multi-client Support**: Multiple users can connect simultaneously and receive real-time updates.
*   **Responsive Design**: Clean interface that adapts to different screen sizes with wider content area for better file list viewing.
*   **CORS and Range Request Support**: Full support for cross-origin requests and HTTP range requests, making it compatible with genomic visualization tools like IGV-Web.
*   **Robust Logging**: Features log rotation, compression, and retention with detailed file system monitoring.
*   **Intelligent Caching**: Configuration and file data are cached for optimal performance.

## Production Deployment

This guide provides instructions for deploying the application in a production environment.

### Prerequisites

*   **A server**: A Linux server (e.g., Ubuntu, Debian) with a public IP address.
*   **A domain name**: A domain name pointing to your server's IP address.
*   **Caddy**: The Caddy web server. You can find installation instructions at [caddyserver.com](https://caddyserver.com/docs/install).
*   **Python**: Python 3.8 or higher (recommended).
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

```caddy
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

    # CORS headers for all static files (required for IGV-Web)
    header @static {
        Access-Control-Allow-Origin "*"
        Access-Control-Allow-Methods "GET, HEAD, OPTIONS"
        Access-Control-Allow-Headers "Range, Content-Type, Accept-Encoding"
        Access-Control-Expose-Headers "Content-Length, Content-Range, Accept-Ranges"
    }

    # Handle CORS preflight requests
    @options {
        method OPTIONS
    }
    respond @options 204

    route {
        file_server @static {
            root /path/to/your/project/static
        }
        # Disable buffering for SSE endpoints
        @sse {
            path /api/events
        }
        reverse_proxy @sse localhost:5000 {
            flush_interval -1
        }
        # Proxy all other requests to the Python backend
        reverse_proxy localhost:5000
    }
}
```

**Note**: Make sure to replace `/path/to/your/project` with the actual absolute path to the project directory on your server.

#### Configuration System

The application supports dynamic configuration through files in the `config/` directory:

*   **`organisation.txt`**: Organization name displayed in header and footer
*   **`heading.txt`**: Main page heading and browser title
*   **`text.txt`**: Descriptive text shown below the heading
*   **`*.logo.*`**: Logo images (PNG, JPG, SVG, etc.) - files must contain `.logo.` in the name
*   **`*.link`**: Footer links - each file should contain URL on first line, display text on second line

#### Python Backend Configuration

The Python application can be configured with the following command-line arguments:
*   `--static-folder`: The path to the directory containing the files you want to serve. Defaults to `./static`. Supports recursive subdirectories.
*   `--log-file`: The path to the file where logs should be written. Defaults to `./logs/app.log`.

For production, it's recommended to serve files from a standard web directory like `/var/www/fserver` and write logs to `/var/log/file_server/app.log`.

First, create the necessary directories and set the correct permissions (replace `your-user` with the user that will run the application):

```bash
# Create the web directory
sudo mkdir -p /var/www/fserver
sudo chown your-user:your-user /var/www/fserver

# Create the log directory
sudo mkdir -p /var/log/file_server
sudo chown your-user:your-user /var/log/file_server
```

### 3. Running the Application

For a production environment, it's important to run the Python backend as a service to ensure it's always running. Here's an example of how to do this using `systemd`.

#### Create a systemd Service File

Create a new file at `/etc/systemd/system/melissa.service` with the following content.

```ini
[Unit]
Description=Melissa Static File Server
After=network.target

[Service]
User=your-user
Group=your-group
WorkingDirectory=/path/to/your/project
# Note the command-line arguments
ExecStart=/path/to/your/project/.venv/bin/python app/main.py --static-folder /var/www/fserver --log-file /var/log/file_server/app.log
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

And view the logs with:

```bash
sudo journalctl -u melissa.service
# Or view the log file directly
sudo tail -f /var/log/file_server/app.log
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

## Local Development

For local development, you can run the application directly:

```bash
# Start the Python backend
source .venv/bin/activate
python app/main.py &

# Start Caddy (in a separate terminal)
caddy run
```

Then visit `http://localhost:8080` to access the application.

## File Organization

Files in the `static/` directory can be organized in subdirectories of any depth. All files will be recursively scanned and displayed in a collapsible tree structure:

- 📁 **Directories**: Shown with folder icons and expand/collapse arrows (▶/▼)
- 📄 **Files**: Displayed with file icons and full relative paths (e.g., `datasets/genome/sample1.vcf`)
- **Navigation**: Click directories to expand/collapse their contents
- **File Sizes**: Displayed in human-readable format (KB, MB, GB)
- **Checksums**: MD5 hashes calculated automatically for integrity verification
- **Descriptions**: Optional `description.txt` files provide context for directories

## Customization

### Adding Logos
Place image files with `.logo.` in the filename in the `config/` directory:
- `01_company.logo.png` - Company logo
- `02_partner.logo.svg` - Partner logo

### Adding Footer Links
Create `.link` files in the `config/` directory:
```
https://example.com
Example Link Text
```

### Updating Content
Modify the text files in `config/` to customize the interface:
- Edit `organisation.txt` for the organization name
- Edit `heading.txt` for the main page title
- Edit `text.txt` for the description

Changes to configuration files are automatically detected and cached for performance.

## Genomic Data Compatibility

This server is specifically configured for hosting genomic data files compatible with visualization tools like IGV-Web:

### Supported Features
- **CORS Headers**: Cross-origin resource sharing enabled for web-based genomic viewers
- **HTTP Range Requests**: Efficient streaming of large files (BAM, VCF, BigWig, etc.)
- **File Integrity**: MD5 checksums for data verification
- **Directory Organization**: Structured file organization with descriptions

### IGV-Web Integration
The server meets all requirements for IGV-Web track hosting:
- Proper CORS configuration with exposed headers
- Range request support for efficient file streaming
- No additional configuration needed

### Adding Directory Descriptions
Create `description.txt` files in directories to provide contextual information:

#### Root Level Description
```
static/description.txt
```
Content appears below the table header for all users.

#### Directory-Specific Descriptions
```
static/datasets/description.txt
static/genomics/samples/description.txt
```
Content appears when the directory is expanded, providing context about the files within.

**Features:**
- Descriptions are automatically detected and cached
- Support for any directory depth
- Real-time updates when description files are modified
- Clean formatting with visual distinction from file entries
- Description files are hidden from the file tree display

## Acknowledgements

This project was created with the assistance of Google's Gemini and enhanced with Claude Code.
