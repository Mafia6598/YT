"""One-time Google OAuth for YouTube. Run on a machine with a browser:

    cd backend && python -m app.youtube.auth            # opens browser
    cd backend && python -m app.youtube.auth --console  # prints a URL (headless servers)
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from ..config import get_settings
from . import SCOPES


def main() -> None:
    s = get_settings()
    if not s.google_client_secrets_file.exists():
        sys.exit(f"Missing {s.google_client_secrets_file}. Download the OAuth client JSON "
                 "(type: Desktop app) from Google Cloud Console. See docs/API_INTEGRATIONS.md")
    flow = InstalledAppFlow.from_client_secrets_file(str(s.google_client_secrets_file), SCOPES)
    if "--console" in sys.argv:
        print("Headless mode. On YOUR computer open an SSH tunnel first:\n"
              "    ssh -L 8765:localhost:8765 <user>@<this-server>\n"
              "then open the URL below in your browser; Google redirects to localhost:8765 through the tunnel.")
        creds = flow.run_local_server(port=8765, open_browser=False)
    else:
        creds = flow.run_local_server(port=0)
    s.youtube_token_file.parent.mkdir(parents=True, exist_ok=True)
    s.youtube_token_file.write_text(creds.to_json())
    s.youtube_token_file.chmod(0o600)
    print(f"Saved token to {s.youtube_token_file}")


if __name__ == "__main__":
    main()
