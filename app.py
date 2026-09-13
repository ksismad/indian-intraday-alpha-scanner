# Streamlit-only on-demand entrypoint.
# All scanner execution is performed by app_on_demand.py while the page is open.
from app_on_demand import dashboard  # noqa: F401
