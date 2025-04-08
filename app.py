import streamlit as st
import requests
from phi.tools.firecrawl import FirecrawlTools
from firecrawl import FirecrawlApp
from pydantic import BaseModel, Field
from typing import List
import json
import pandas as pd
from io import BytesIO
import re
from datetime import datetime, timedelta

class QuoraUserInteractionSchema(BaseModel):
    username: str = Field(description="The username of the user who posted the question or answer")
    bio: str = Field(description="The bio or description of the user")
    post_type: str = Field(description="The type of post, either 'question' or 'answer'")
    timestamp: str = Field(description="When the question or answer was posted")
    upvotes: int = Field(default=0, description="Number of upvotes received")
    links: List[str] = Field(default_factory=list, description="Any links included in the post")

class QuoraPageSchema(BaseModel):
    interactions: List[QuoraUserInteractionSchema] = Field(description="List of all user interactions (questions and answers) on the page")

class GroqAgent:
    """
    Custom Groq Agent to replace Phidata's model integration
    """
    def __init__(self, api_key: str, model: str = "llama3-70b-8192"):
        self.api_key = api_key
        self.model = model
        self.system_prompt = ""
        self.client = None
        
        # Only create the client if we have a valid API key
        if api_key and api_key.strip():
            try:
                from groq import Groq
                self.client = Groq(api_key=api_key.strip())
                # Add a print to confirm client creation
                print("Groq client created successfully")
            except ImportError:
                print("Error: groq package not installed")
                # Handle missing dependency silently - we'll check for client in run()
                pass
            except Exception as e:
                print(f"Error initializing Groq client: {e}")
                # Handle initialization error silently - we'll check for client in run()
                pass

    def run(self, prompt: str):
        """
        Run Groq inference with fallback to a simple keyword extraction
        """
        result = None
        
        if self.client:
            try:
                response = self.client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    model=self.model
                )
                result = response.choices[0].message.content
                print(f"Groq API returned: {result}")
            except Exception as e:
                print(f"Groq API error: {e}")
                result = None
        
        # Fallback to simple keyword extraction if API call failed or client doesn't exist
        if not result:
            import re
            print("Using fallback keyword extraction")
            words = re.findall(r'\b\w+\b', prompt.lower())
            # Filter out common words
            common_words = ['a', 'an', 'the', 'for', 'and', 'or', 'of', 'to', 'in', 'who', 'what', 'where', 'when', 'looking', 'need', 'generate', 'find', 'this', 'query', 'into', 'concise', 'description', 'transform']
            filtered_words = [w for w in words if w not in common_words and len(w) > 2]
            
            # Take the most relevant keywords
            keywords = filtered_words[:4] if len(filtered_words) > 4 else filtered_words
            result = " ".join(keywords)
            print(f"Keyword extraction produced: {result}")
            
        return result

def search_for_urls(company_description: str, firecrawl_api_key: str, num_links: int) -> List[str]:
    url = "https://api.firecrawl.dev/v1/search"
    headers = {
        "Authorization": f"Bearer {firecrawl_api_key}",
        "Content-Type": "application/json"
    }
    query1 = f"quora websites where people are looking for {company_description} services"
    payload = {
        "query": query1,
        "limit": num_links,
        "lang": "en",
        "location": "United States",
        "timeout": 60000,
    }
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        data = response.json()
        if data.get("success"):
            results = data.get("data", [])
            return [result["url"] for result in results]
    return []

def extract_user_info_from_urls(urls: List[str], firecrawl_api_key: str) -> List[dict]:
    user_info_list = []
    firecrawl_app = FirecrawlApp(api_key=firecrawl_api_key)
    
    try:
        for url in urls:
            response = firecrawl_app.extract(
                [url],
                {
                    'prompt': 'Extract all user information including username, bio, post type (question/answer), timestamp, upvotes, and any links from Quora posts. Focus on identifying potential leads who are asking questions or providing answers related to the topic.',
                    'schema': QuoraPageSchema.model_json_schema(),
                }
            )
            
            if response.get('success') and response.get('status') == 'completed':
                interactions = response.get('data', {}).get('interactions', [])
                if interactions:
                    user_info_list.append({
                        "website_url": url,
                        "user_info": interactions
                    })
    except Exception as e:
        st.error(f"Error extracting user info: {e}")
    
    return user_info_list

def format_user_info_to_flattened_json(user_info_list: List[dict]) -> List[dict]:
    flattened_data = []
    
    for info in user_info_list:
        website_url = info["website_url"]
        user_info = info["user_info"]
        
        for interaction in user_info:
            flattened_interaction = {
                "Username": interaction.get("username", ""),
                "Bio": interaction.get("bio", ""),
                "Post Type": interaction.get("post_type", ""),
                "Timestamp": interaction.get("timestamp", ""),
                "Upvotes": interaction.get("upvotes", 0),
                "Links": ", ".join(interaction.get("links", [])),
                "Data Source": "Quora",
                "Website URL": website_url  # Moved to last column
            }
            flattened_data.append(flattened_interaction)
    
    return flattened_data

def create_prompt_transformation_agent(groq_api_key: str):
    # Create custom Groq agent
    agent = GroqAgent(api_key=groq_api_key)
    agent.system_prompt = """You are an expert at transforming detailed user queries into concise company descriptions.
Your task is to extract the core business/product focus in 3-4 words.

Examples:
Input: "Generate leads looking for AI-powered customer support chatbots for e-commerce stores."
Output: "AI customer support chatbots for e commerce"

Input: "Find people interested in voice cloning technology for creating audiobooks and podcasts"
Output: "voice cloning technology"

Input: "Looking for users who need automated video editing software with AI capabilities"
Output: "AI video editing software"

Input: "Need to find businesses interested in implementing machine learning solutions for fraud detection"
Output: "ML fraud detection"

Always focus on the core product/service and keep it concise but clear."""
    
    return agent

def download_data_as_excel(data):
    """Generate downloadable Excel file from lead data"""
    df = pd.DataFrame(data)
    # Set index to start from 1 for Excel file as well
    df.index = range(1, len(df) + 1)
    
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, sheet_name='Leads')
        # Adjust column widths
        worksheet = writer.sheets['Leads']
        for i, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
            worksheet.set_column(i+1, i+1, max_len)  # i+1 because column 0 is now the index
    output.seek(0)
    return output.getvalue()

def normalize_timestamp(timestamp_str):
    """Attempt to normalize timestamp strings into time periods"""
    timestamp_str = timestamp_str.lower() if timestamp_str else ""
    
    # Try to extract timeframes
    if "hour" in timestamp_str or "hr" in timestamp_str:
        return "Last 24 hours"
    elif "day" in timestamp_str or "yesterday" in timestamp_str:
        return "Last week"
    elif "week" in timestamp_str:
        return "Last month"
    elif "month" in timestamp_str:
        return "Last quarter"
    elif "year" in timestamp_str:
        return "Last year"
    else:
        return "Unknown"

def display_leads_table(flattened_data):
    """Display lead data in a neatly formatted table"""
    if not flattened_data:
        st.warning("No lead data available to display.")
        return
    
    # Convert to DataFrame for better display
    df = pd.DataFrame(flattened_data)
    
    # Set index to start from 1 instead of 0
    df.index = range(1, len(df) + 1)
    
    # Create three metrics in a row
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Leads Found", len(flattened_data))
    with col2:
        st.metric("Sources", "Quora")
    with col3:
        st.metric("Unique Users", len(df['Username'].unique()))
    
    # Create tabs for different views of the data
    tab1, tab2 = st.tabs(["📋 All Leads", "📊 Analytics"])
    
    with tab1:
        # Add search functionality
        search_term = st.text_input("🔍 Search leads", placeholder="Filter by username or bio")
        if search_term:
            filtered_df = df[df['Username'].str.contains(search_term, case=False) | 
                             df['Bio'].str.contains(search_term, case=False)]
            st.dataframe(filtered_df, use_container_width=True)
        else:
            st.dataframe(df, use_container_width=True)
    
    with tab2:
        # Only show Top Users by Upvotes
        if not df.empty:
            st.subheader("Top Users by Upvotes")
            top_users = df.nlargest(5, 'Upvotes')[['Username', 'Upvotes']]
            st.bar_chart(top_users, x='Username', y='Upvotes')
    
    # Add Excel download button
    excel_data = download_data_as_excel(flattened_data)
    st.download_button(
        label="📊 Download Excel",
        data=excel_data,
        file_name="quora_leads.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def setup_page_config():
    # Set page config at the very beginning
    st.set_page_config(
        page_title="Leadify - AI Lead Generation",
        page_icon="🎯",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Custom CSS for better styling
    st.markdown("""
    <style>
    .main-title {
        font-size: 3rem !important;
        font-weight: 700 !important;
        color: #0F52BA !important;
        margin-bottom: 0.5rem !important;
    }
    .sub-title {
        font-size: 1.2rem !important;
        color: #555 !important;
        margin-bottom: 2rem !important;
    }
    .api-header {
        margin-top: 1.5rem !important;
        font-weight: 600 !important;
    }
    .stButton > button {
        background-color: #0F52BA !important;
        color: white !important;
        border-radius: 6px !important;
        padding: 0.5rem 1rem !important;
        font-weight: 600 !important;
    }
    .stTextInput > div > div > input {
        border-radius: 6px !important;
    }
    .st-emotion-cache-16txtl3 {
        padding: 3rem 1rem !important;
    }
    div[data-testid="stSidebarContent"] {
        background-color: #f8f9fa !important;
    }
    div.stTabs [data-baseweb="tab-list"] {
        gap: 2px;
    }
    div.stTabs [data-baseweb="tab"] {
        background-color: #f0f2f6;
        border-radius: 4px 4px 0px 0px;
        padding: 10px 20px;
        gap: 1px;
    }
    div.stTabs [aria-selected="true"] {
        background-color: #0F52BA;
        color: white;
    }
    </style>
    """, unsafe_allow_html=True)


def main():
    setup_page_config()
    
    # Header section with custom styled title
    st.markdown('<p class="main-title">🎯 Leadify</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-title">AI-powered lead generation from Quora conversations</p>', unsafe_allow_html=True)
    
    # Create containers for different sections
    setup_container = st.container()
    query_container = st.container()
    results_container = st.container()
    
    # Initialize session state for tracking app state
    if 'search_completed' not in st.session_state:
        st.session_state.search_completed = False
    if 'company_description' not in st.session_state:
        st.session_state.company_description = ""
    if 'urls' not in st.session_state:
        st.session_state.urls = []
    if 'flattened_data' not in st.session_state:
        st.session_state.flattened_data = []
    

    # Sidebar for configuration
    with st.sidebar:
        st.markdown('<p class="api-header">🔑 API Configuration</p>', unsafe_allow_html=True)
        
        # Create expandable sections for API keys
        with st.expander("Firecrawl API Key", expanded=True):
            firecrawl_api_key = st.text_input("Enter your Firecrawl key", type="password", help="Required for web search", key="firecrawl_key")
            st.caption("[Get your Firecrawl API key](https://www.firecrawl.dev/app/api-keys)")
        
        with st.expander("Groq API Key", expanded=True):
            groq_api_key = st.text_input("Enter your Groq key", type="password", help="Required for AI processing", key="groq_key")
            st.caption("[Get your Groq API key](https://console.groq.com/keys)")
        
        st.markdown('<p class="api-header">⚙️ Search Settings</p>', unsafe_allow_html=True)
        num_links = st.slider("Number of links to search", min_value=1, max_value=10, value=3)
        
        # Add reset functionality
        if st.button("Reset Application", use_container_width=True):
            st.session_state.clear()
            st.rerun()
        
        # Add credits section at bottom
        st.markdown("---")
        st.caption("Leadify - Powered by Firecrawl & Groq")
    
    # Main query input area with card-like styling
    with query_container:
        st.markdown("### 🔍 What leads are you looking for?")
        
        col1, col2 = st.columns([3, 1])
        with col1:
            user_query = st.text_area(
                "Describe your target customer:",
                placeholder="e.g., Find people who need graphic designers for designing book covers",
                help="Be specific about the product/service and target audience.",
                height=100
            )
        with col2:
            st.write("")
            st.write("")
            search_button = st.button("🔎 Generate Leads", use_container_width=True)
    
    # Process search when button is clicked
    if search_button:
        if not firecrawl_api_key:
            st.error("⚠️ Please provide a Firecrawl API key.")
        elif not user_query:
            st.error("⚠️ Please describe what leads you're looking for.")
        else:
            # Clear previous results
            st.session_state.search_completed = False
            st.session_state.urls = []
            st.session_state.flattened_data = []
            
            # Step 1: Transform query
            with st.spinner("🧠 Processing your query..."):
                transform_agent = create_prompt_transformation_agent(groq_api_key)
                company_description = transform_agent.run(f"Transform this query into a concise 3-4 word company description: {user_query}")
                st.session_state.company_description = company_description
                # Add debugging output
                print(f"Company description set to: {company_description}")
            
            # Step 2: Search for URLs
            with st.spinner("🔎 Searching for relevant discussions..."):
                urls = search_for_urls(company_description, firecrawl_api_key, num_links)
                st.session_state.urls = urls
            
            if urls:
                # Step 3: Extract user info
                with st.spinner("👤 Identifying potential leads..."):
                    user_info_list = extract_user_info_from_urls(urls, firecrawl_api_key)
                
                # Step 4: Format data
                with st.spinner("📊 Preparing lead data..."):
                    flattened_data = format_user_info_to_flattened_json(user_info_list)
                    st.session_state.flattened_data = flattened_data
                
                st.session_state.search_completed = True
                st.rerun()  # Rerun to display results in the proper containers
            else:
                st.error("No relevant discussions found. Try modifying your search query.")
    
    # Display results if search was completed
    if st.session_state.search_completed:
        with results_container:
            st.markdown("---")
            st.subheader(f"🎯 Results for: {st.session_state.company_description}")
            
            # Display URLs in collapsible section
            with st.expander("🔗 Quora Links Used", expanded=False):
                for i, url in enumerate(st.session_state.urls, 1):
                    st.markdown(f"{i}. [{url}]({url})")
            
            # Display lead data
            if st.session_state.flattened_data:
                display_leads_table(st.session_state.flattened_data)
                st.success("✅ Lead generation completed successfully!")
            else:
                st.warning("⚠️ No lead data could be extracted from the URLs. Try adjusting your search query.")

if __name__ == "__main__":
    main()