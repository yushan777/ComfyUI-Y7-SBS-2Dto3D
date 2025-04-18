# from .nodes.brightness import Y7Nodes_Brightness
# from .nodes.template_node import Y7_TemplateNode
from .nodes.documentation import format_descriptions
from .nodes.sbs import Y7_SideBySide, Y7_VideoSideBySide


NODE_CLASS_MAPPINGS = {
    "Y7_SideBySide": Y7_SideBySide,
    "Y7_VideoSideBySide": Y7_VideoSideBySide,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "Y7_SideBySide": "Y7 SBS (Image)",
    "Y7_VideoSideBySide": "Y7 SBS (Video)",
}

# Apply HTML-formatted documentation to node classes
# This sets the DESCRIPTION and description properties for each node class
# using the predefined descriptions in documentation.py
format_descriptions(NODE_CLASS_MAPPINGS)

# Make sure these are accessible to ComfyUI
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

# Tell ComfyUI where to find the web/JavaScript files
WEB_DIRECTORY = "./web"

#  =============================================================================
# these imports are for server route registration
from aiohttp import web
from server import PromptServer
from pathlib import Path
import os
import json

# Add server routes if PromptServer is available
if hasattr(PromptServer, "instance") and not PromptServer.instance.app.frozen:
    # detect directory name at runtime since it could be different from the 
    # Repo-name depending on whether installed via the ComfyUI Manager or a git clone
    # Get the actual directory name (last part of the absolute path)
    current_dir = Path(__file__).parent.absolute()
    dir_name = os.path.basename(current_dir)
    # print(f"{dir_name}")


    # Register routes using the actual directory name
    PromptServer.instance.app.add_routes([
        # Route for JavaScript files
        web.static(f"/{dir_name}", (current_dir / "web").as_posix())


    # # Register routes for web assets and data files
    # PromptServer.instance.app.add_routes([
    #     # Route for JavaScript files
    #     web.static("/ComfyUI-Y7Nodes", (Path(__file__).parent.absolute() / "web").as_posix())

    ])
