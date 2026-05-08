import sys

import streamlit as st
import streamlit_antd_components as sac

from server.utils.utils import api_address
from web.knowledge_base.knowledge_base import knowledge_base_page
from web.utils.utils import ApiRequest

api = ApiRequest(base_url=api_address())

if __name__ == "__main__":

    st.markdown(
        """
        <style>
        [data-testid="stSidebarUserContent"] {
            padding-top: 20px;
        }
        .block-container {
            padding-top: 25px;
        }
        [data-testid="stBottomBlockContainer"] {
            padding-bottom: 20px;
        }
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:

        selected_page = sac.menu(
            [
                sac.MenuItem("知识库管理", icon="hdd-stack"),
            ],
            key="selected_page",
            open_index=0,
        )

        sac.divider()

    if selected_page == "知识库管理":
        knowledge_base_page(api=api)
