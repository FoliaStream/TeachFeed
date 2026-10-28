# Custom sidebar
HIDE_SIDEBAR_NAV = """
<style>
    /* Hide the default multi-page navigation */
    [data-testid="stSidebarNav"] {
        display: none;
    }
    
    /* Make the custom sidebar navigation more prominent */
    .sidebar .sidebar-content {
        padding-top: 2rem;
    }
</style>
"""

BRAND = {
    "blue":   "#2B6CA3",   # "Teach"
    "teal":   "#3A9A9F",   # "Feed"
    "cyan":   "#19E3D6",   # arrow highlight
    "bg":     "#F7F9F4",   # logo background
    "text":   "#1F3B57",
    "muted":  "#6B8499",
}

SIDEBAR_STYLES = {
    "container": {"padding": "0.75rem", "background-color": "transparent"},
    "icon": {"color": BRAND["muted"], "font-size": "18px"},
    "nav-link": {
        "font-size": "15px", "font-weight": "500", "text-align": "left",
        "margin": "4px 0px", "padding": "10px 14px", "border-radius": "10px",
        "color": BRAND["text"], "--hover-color": "rgba(58, 154, 159, 0.10)",
    },
    "nav-link-selected": {
        "background": f"linear-gradient(135deg, {BRAND['blue']}, {BRAND['teal']})",
        "color": "#FFFFFF", "font-weight": "600",
        "box-shadow": "0 4px 12px rgba(43, 108, 163, 0.25)",
    },
}

CATEGORY_COLORS = {
    "language": "blue",
    "delivery": "violet",
    "content": "green",
    "structure": "orange",
    "other": "gray",
}