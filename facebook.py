
import os
import re
import time
import html
import mimetypes
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests

from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

BUFFER_API_URL = "https://api.buffer.com"
BUFFER_API_KEY = os.getenv("BUFFER_API_KEY", "YOUR_BUFFER_API_KEY")
BUFFER_CHANNEL_ID = os.getenv(
    "BUFFER_CHANNEL_ID",
    "BUFFER_FACEBOOK_CHANNEL_ID"
)

WORDPRESS_API_URL = (
    "https://americamornings.com/wp-json/wp/v2/get-posts"
)

MAX_POSTS = 30
DELAY_SECONDS = 10
IMAGE_FOLDER = Path(__file__).resolve().parent / "featured_images"

IMAGE_FOLDER.mkdir(parents=True, exist_ok=True)

session = requests.Session()

CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
    createPost(input: $input) {
        ... on PostActionSuccess {
            post {
                id
                text
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
    """Convert API values into usable plain text."""

    if isinstance(value, dict):
        value = value.get("rendered", "")

    if not isinstance(value, str):
        return ""

    value = html.unescape(value).strip()

    # Handle Markdown links such as [URL](URL).
    value = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r"\2",
        value
    )

    return value.strip()


# def get_article_text(article):
#     """Build the Facebook post text."""

#     title = clean_value(article.get("title"))
#     content = clean_value(article.get("content"))
#     permalink = clean_value(article.get("permalink"))

#     if not title:
#         raise ValueError("Article title is missing.")

#     if not permalink:
#         raise ValueError("Article permalink is missing.")

#     # Title + full content + article URL
#     post_text = f"{title}\n\n{content}\n\n{permalink}"

#     return title, post_text, permalink



def get_article_text(article):
    """Build Facebook post text with a 250-character content limit."""

    title = clean_value(article.get("title"))
    content = clean_value(article.get("content"))
    permalink = clean_value(article.get("permalink"))

    if not title:
        raise ValueError("Article title is missing.")

    if not permalink:
        raise ValueError("Article permalink is missing.")

    # Keep only the first 250 characters of article content.
    if len(content) > 250:
        content = content[:250].rstrip() + "...."

    post_text = f"{title}\n\n{content}\n\n{permalink}"

    return title, post_text, permalink



def download_featured_image(image_url, post_id):
    """Download the featured image to the local folder."""

    image_url = clean_value(image_url)

    if not image_url:
        print("No featured image found.")
        return None

    if not image_url.startswith(("https://", "http://")):
        print(f"Invalid featured image URL: {image_url}")
        return None

    try:
        response = session.get(
            image_url,
            timeout=60,
            stream=True
        )
        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type", ""
        ).split(";")[0].lower()

        # Prefer the actual response MIME type.
        extension = mimetypes.guess_extension(content_type)

        if not extension:
            extension = Path(
                unquote(urlparse(image_url).path)
            ).suffix

        if not extension or len(extension) > 10:
            extension = ".jpg"

        image_path = IMAGE_FOLDER / f"{post_id}{extension}"

        with open(image_path, "wb") as image_file:
            for chunk in response.iter_content(
                chunk_size=1024 * 64
            ):
                if chunk:
                    image_file.write(chunk)

        print(f"Image downloaded: {image_path}")
        return image_path

    except requests.RequestException as error:
        print(f"Image download failed: {error}")
        return None


# --------------------------------------------------
# BUFFER GRAPHQL
# --------------------------------------------------

def buffer_request(query, variables=None):
    headers = {
        "Authorization": f"Bearer {BUFFER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {"query": query}

    if variables is not None:
        payload["variables"] = variables

    response = session.post(
        BUFFER_API_URL,
        headers=headers,
        json=payload,
        timeout=60
    )

    response.raise_for_status()
    result = response.json()

    if result.get("errors"):
        raise RuntimeError(
            f"Buffer GraphQL error: {result['errors']}"
        )

    return result


def publish_to_facebook(article):
    post_id = article.get("id", "unknown")

    title, post_text, permalink = get_article_text(article)

    image_url = clean_value(
        article.get("featured_image")
    )

    print(f"Article: {title}")

    # Save a local copy of the featured image.
    if image_url:
        download_featured_image(image_url, post_id)

    post_input = {
        "text": post_text,
        "channelId": BUFFER_CHANNEL_ID,
        "schedulingType": "automatic",
        "mode": "shareNow",
        "metadata": {
            "facebook": {
                "type": "post"
            }
        }
    }

    # IMPORTANT:
    # Buffer needs an image URL it can access.
    # A local path on your computer is not accessible to Buffer.
    # Therefore, use the original public WordPress image URL.
    if image_url:
        post_input["assets"] = [
            {
                "image": {
                    "url": image_url
                }
            }
        ]

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

    print(f"SUCCESS: {title}")
    print(f"Buffer post ID: {post['id']}")
    print(f"Article URL: {permalink}")

    return post


# --------------------------------------------------
# WORDPRESS
# --------------------------------------------------

def fetch_wordpress_posts():
    response = session.get(
        WORDPRESS_API_URL,
        timeout=60
    )

    response.raise_for_status()

    data = response.json()

    if not isinstance(data, list):
        raise ValueError(
            "WordPress API did not return a JSON list."
        )

    # Your endpoint returns posts; use only the first 30.
    # This assumes the endpoint orders posts newest first.
    articles = data[:MAX_POSTS]

    print(f"WordPress returned {len(data)} posts.")
    print(f"Will process {len(articles)} latest posts.")

    return articles


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():
    if (
        not BUFFER_API_KEY
        or BUFFER_API_KEY == "YOUR_BUFFER_API_KEY"
        or not BUFFER_CHANNEL_ID
        or BUFFER_CHANNEL_ID == "YOUR_FACEBOOK_CHANNEL_ID"
    ):
        raise SystemExit(
            "Please configure BUFFER_API_KEY and "
            "BUFFER_CHANNEL_ID first."
        )

    try:
        articles = fetch_wordpress_posts()
    except (requests.RequestException, ValueError) as error:
        raise SystemExit(
            f"Could not fetch WordPress posts: {error}"
        )

    if not articles:
        print("No WordPress posts found.")
        return

    successful = 0
    failed = 0

    for index, article in enumerate(articles, start=1):
        print(f"\n[{index}/{len(articles)}] Publishing article...")

        try:
            publish_to_facebook(article)
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

    print("\n========== SUMMARY ==========")
    print(f"Total processed: {len(articles)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Downloaded images folder: {IMAGE_FOLDER}")


if __name__ == "__main__":
    main()
