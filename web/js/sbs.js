import { app } from "../../../scripts/app.js";

// Default node size (adjust as needed)
const DEFAULT_IMAGE_SBS_NODE_WIDTH = 240;  // For Y7_SideBySide
const DEFAULT_IMAGE_SBS_NODE_HEIGHT = 150; // For Y7_SideBySide

const DEFAULT_VIDEO_SBS_NODE_WIDTH = 250;  // For Y7_VideoSideBySide
const DEFAULT_VIDEO_SBS_NODE_HEIGHT = 150; // For Y7_VideoSideBySide
app.registerExtension({
    // Unique name for the extension
    name: "Y7.SideBySideNodes", // Corrected typo in name
    async beforeRegisterNodeDef(nodeType, nodeData, app) {

        // Check if the node being registered is one of our target nodes
        // This must match the class mapping names in __init__.py
        if (nodeData.name === "Y7_SideBySide" || nodeData.name === "Y7_VideoSideBySide") {

            // Store the original onNodeCreated method
            const onNodeCreated = nodeType.prototype.onNodeCreated;

            // When a new instance of this node type is created...
            nodeType.prototype.onNodeCreated = function() {
                // Call the original method first
                onNodeCreated?.apply(this, arguments);

                // ==========================================================
                // SET INITIAL NODE SIZE
                // ==========================================================
                // Set different sizes based on node type
                let newWidth, newHeight;
                
                // console.log("nodeData.name = " + nodeData.name)

                if (nodeData.name === "Y7_SideBySide") {
                    newWidth = Math.max(this.size[0], DEFAULT_IMAGE_SBS_NODE_WIDTH);
                    newHeight = Math.max(this.size[1], DEFAULT_IMAGE_SBS_NODE_HEIGHT);
                } else if (nodeData.name === "Y7_VideoSideBySide") {
                    newWidth = Math.max(this.size[0], DEFAULT_VIDEO_SBS_NODE_WIDTH);
                    newHeight = Math.max(this.size[1], DEFAULT_VIDEO_SBS_NODE_HEIGHT);
                }

                // Check if the size needs to be updated
                if (this.size[0] !== newWidth || this.size[1] !== newHeight) {
                    this.size[0] = newWidth;
                    this.size[1] = newHeight;

                    // Apply the resize if the method exists
                    if (this.onResize) {
                        this.onResize(this.size);
                    }

                    // Tell ComfyUI to redraw the canvas
                    app.graph.setDirtyCanvas(true, false);
                }
            };
        }
    },
});
