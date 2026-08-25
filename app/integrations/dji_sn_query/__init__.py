"""DJI serial-number lookup integration.

The integration deliberately uses a visible, dedicated browser profile.  It
does not bypass CAPTCHA challenges and does not reuse the user's daily browser
profile.
"""

from .models import SNQueryResult
from .parser import normalize_device_response, parse_device_page_text

__all__ = ["SNQueryResult", "normalize_device_response", "parse_device_page_text"]
