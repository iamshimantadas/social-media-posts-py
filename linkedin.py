
import os
import re
import time
import html
import requests

from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

BUFFER_API_URL = "https://api.buffer.com"

BUFFER_API_KEY = os.getenv("BUFFER_API_KEY", "")
BUFFER_LINKEDIN_CHANNEL_ID = os.getenv(
    "BUFFER_LINKEDIN_CHANNEL_ID", ""
)

WORDPRESS_API_URL = (
    "https://americamornings.com/wp-json/wp/v2/get-posts"
)

# Maximum articles to process per run.
MAX_POSTS = 40

# Delay between publishing attempts.
DELAY_SECONDS = 10

# Maximum text length for a LinkedIn post.
MAX_POST_TEXT_LENGTH = 2000

REQUEST_TIMEOUT = 60

session = requests.Session()

# --------------------------------------------------
# BUFFER GRAPHQL MUTATION
# --------------------------------------------------

CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
    createPost(input: $input) {
        ... on PostActionSuccess {
            post {
                id
                text
                dueAt
                assets {
                    id
                    mimeType
                }
            }
        }
        ... on MutationError {
            message
        }
    }
}
"""

# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def clean_value(value):
    """Convert WordPress values into clean plain text."""

    if isinstance(value, dict):
        value = value.get("rendered", "")

    if not isinstance(value, str):
        return ""

    value = html.unescape(value)

    # Remove HTML tags.
    value = re.sub(r"<[^>]+>", " ", value)

    # Normalize whitespace.
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def get_article_text(article):
    """
    Build a LinkedIn post with:
    Title + short description + permalink.

    Maximum length: 2000 characters.
    """

    title = clean_value(article.get("title"))
    content = clean_value(
        article.get("excerpt")
        or article.get("description")
        or article.get("content")
    )
    permalink = clean_value(article.get("permalink"))

    if not title:
        raise ValueError("Article title is missing.")

    if not permalink:
        raise ValueError("Article permalink is missing.")

    # Reserve space for the title, separators and full URL.
    fixed_length = len(title) + len(permalink) + 4

    if fixed_length > MAX_POST_TEXT_LENGTH:
        raise ValueError(
            "Title and permalink exceed the 2000-character limit."
        )

    available_length = MAX_POST_TEXT_LENGTH - fixed_length

    # Shorten the description to fit the maximum length.
    if len(content) > available_length:
        if available_length >= 4:
            content = content[:available_length - 3].rstrip() + "..."
        else:
            content = content[:available_length]

    post_text = f"{title}\n\n{content}\n\n{permalink}"

    if len(post_text) > MAX_POST_TEXT_LENGTH:
        raise ValueError("Post exceeds the 2000-character limit.")

    return title, post_text, permalink


# --------------------------------------------------
# WORDPRESS API
# --------------------------------------------------

def fetch_wordpress_posts():
    """Fetch articles from the WordPress REST API."""

    response = session.get(
        WORDPRESS_API_URL,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    try:
        articles = response.json()
    except ValueError as error:
        raise RuntimeError(
            "WordPress returned invalid JSON."
        ) from error

    if not isinstance(articles, list):
        raise ValueError(
            "WordPress API did not return a JSON list."
        )

    articles = articles[:MAX_POSTS]

    print(f"WordPress articles available: {len(articles)}")

    return articles


# --------------------------------------------------
# BUFFER GRAPHQL REQUEST
# --------------------------------------------------

def buffer_request(query, variables):
    """Send a GraphQL request to Buffer."""

    headers = {
        "Authorization": f"Bearer {BUFFER_API_KEY}",
        "Content-Type": "application/json",
    }

    response = session.post(
        BUFFER_API_URL,
        headers=headers,
        json={
            "query": query,
            "variables": variables
        },
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    try:
        result = response.json()
    except ValueError as error:
        raise RuntimeError(
            f"Buffer returned invalid JSON: {response.text[:500]}"
        ) from error

    if result.get("errors"):
        raise RuntimeError(
            f"Buffer GraphQL error: {result['errors']}"
        )

    return result


# --------------------------------------------------
# PUBLISH LINKEDIN POST
# --------------------------------------------------

def publish_linkedin_post(article):
    """Publish one WordPress article through Buffer."""

    article_id = article.get("id", "unknown")

    title, post_text, permalink = get_article_text(article)

    image_url = clean_value(
        article.get("featured_image")
        or article.get("featured_image_url")
        or article.get("image")
    )

    print(f"Title: {title}")
    print(f"Post length: {len(post_text)}/2000 characters")

    # Create Buffer post input.
    post_input = {
        "text": post_text,
        "channelId": BUFFER_LINKEDIN_CHANNEL_ID,
        "schedulingType": "automatic",
        "mode": "shareNow",
    }

    # Attach the WordPress featured image.
    if image_url:
        if image_url.startswith(("https://", "http://")):
            post_input["assets"] = [
                {
                    "image": {
                        "url": image_url
                    }
                }
            ]
        else:
            print("WARNING: Invalid featured image URL.")

    result = buffer_request(
        CREATE_POST_MUTATION,
        {"input": post_input}
    )

    create_result = (
        result.get("data", {}).get("createPost") or {}
    )

    if create_result.get("message"):
        raise RuntimeError(create_result["message"])

    post = create_result.get("post")

    if not post or not post.get("id"):
        raise RuntimeError(
            f"Unexpected Buffer response: {result}"
        )

    print("SUCCESS: LinkedIn post created.")
    print(f"WordPress article ID: {article_id}")
    print(f"Buffer post ID: {post['id']}")
    print(f"Article URL: {permalink}")
    print(f"Image URL: {image_url or 'No image'}")
    print(f"Scheduled time: {post.get('dueAt') or 'Not specified'}")

    return post


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    if not BUFFER_API_KEY:
        raise SystemExit(
            "BUFFER_API_KEY is missing from .env"
        )

    if not BUFFER_LINKEDIN_CHANNEL_ID:
        raise SystemExit(
            "BUFFER_LINKEDIN_CHANNEL_ID is missing from .env"
        )

    try:
        articles = fetch_wordpress_posts()
    except (requests.RequestException, ValueError, RuntimeError) as error:
        raise SystemExit(
            f"Could not fetch WordPress articles: {error}"
        )

    if not articles:
        print("No WordPress articles found.")
        return

    successful = 0
    failed = 0

    for index, article in enumerate(articles, start=1):

        print(
            f"\n[{index}/{len(articles)}] "
            "Publishing LinkedIn article..."
        )

        try:
            publish_linkedin_post(article)
            successful += 1

        except (
            requests.RequestException,
            RuntimeError,
            ValueError
        ) as error:
            failed += 1
            print(f"FAILED: {error}")

        if index < len(articles):
            time.sleep(DELAY_SECONDS)

    print("\n========== LINKEDIN SUMMARY ==========")
    print(f"Total processed: {len(articles)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
