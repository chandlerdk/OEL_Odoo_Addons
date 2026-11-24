from . import auth

# Import the dispatcher first so its routing_type='mcp' registers in
# http._dispatchers before any @mcp_route endpoint is bound (the @route assert
# checks the type is known at decoration time).
from . import mcp_dispatcher
from . import mcp_route
from . import mcp
from . import main
from . import download
from . import rate_limiting
from . import response_utils
from . import utils
from . import api
from . import oauth_server
