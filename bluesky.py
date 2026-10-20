
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

BUFFER_API_KEY = os.getenv("BUFFER_API_KEY", "")
BUFFER_CHANNEL_ID = os.getenv("BUFFER_BLUESKY_CHANNEL_ID", "")

WORDPRESS_API_URL = (
    "https://americamornings.com/wp-json/wp/v2/get-posts"
)

# Maximum posts to publish per run.
# Keep this at or below your Bluesky daily limit.
MAX_POSTS = 40

DELAY_SECONDS = 10
MAX_POST_TEXT_LENGTH = 300

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
    """Convert WordPress API values into plain text."""

    if isinstance(value, dict):
        value = value.get("rendered", "")

    if not isinstance(value, str):
        return ""

    value = html.unescape(value).strip()

    # Remove HTML tags if the API returns HTML.
    value = re.sub(r"<[^>]+>", " ", value)

    # Convert Markdown links [text](https://example.com)
    # into their URL.
    value = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r"\2",
        value
    )

    # Normalize whitespace.
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def get_article_text(article):
    """
    Build a Bluesky post containing:
    title + short description + permalink.

    The complete text is limited to 300 characters.
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

    # Reserve space for title, permalink and separators.
    # The description receives the remaining characters.
    fixed_text = f"{title}\n\n\n\n{permalink}"

    available_description_length = (
        MAX_POST_TEXT_LENGTH - len(fixed_text)
    )

    if available_description_length < 0:
        # If title and URL are already too long, shorten
        # the title while preserving the complete permalink.
        title_budget = (
            MAX_POST_TEXT_LENGTH - len(permalink) - 6
        )

        if title_budget <= 0:
            raise ValueError(
                "Permalink is too long for the post limit."
            )

        title = title[:title_budget].rstrip()
        fixed_text = f"{title}\n\n\n\n{permalink}"

        available_description_length = (
            MAX_POST_TEXT_LENGTH - len(fixed_text)
        )

    description = content[:max(0, available_description_length)].rstrip()

    # Add an ellipsis when the description is truncated,
    # provided there is room for it.
    if len(content) > len(description) and available_description_length >= 4:
        description = (
            description[:available_description_length - 4].rstrip()
            + "...."
        )

    post_text = f"{title}\n\n{description}\n\n{permalink}"

    # Final safety check for the character limit.
    if len(post_text) > MAX_POST_TEXT_LENGTH:
        overflow = len(post_text) - MAX_POST_TEXT_LENGTH
        description = description[:max(0, len(description) - overflow)].rstrip()
        post_text = f"{title}\n\n{description}\n\n{permalink}"

    if len(post_text) > MAX_POST_TEXT_LENGTH:
        raise ValueError(
            "Could not fit title, description and permalink "
            "within the 300-character limit."
        )

    return title, post_text, permalink


def download_featured_image(image_url, post_id):
    """Download a local copy of the featured image."""

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
    """Send a GraphQL request to Buffer."""

    if not BUFFER_API_KEY:
        raise RuntimeError("BUFFER_API_KEY is missing from .env.")

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


def publish_to_bluesky(article):
    """Publish one article to the Bluesky channel via Buffer."""

    post_id = article.get("id", "unknown")

    title, post_text, permalink = get_article_text(article)

    image_url = clean_value(
        article.get("featured_image")
        or article.get("featured_image_url")
        or article.get("image")
    )

    print(f"Article: {title}")
    print(f"Post length: {len(post_text)} characters")

    # Download the image locally for backup.
    # Buffer still needs the public URL to attach it.
    if image_url:
        download_featured_image(image_url, post_id)

    post_input = {
        "text": post_text,
        "channelId": BUFFER_CHANNEL_ID,
        "schedulingType": "automatic",
        "mode": "shareNow",
    }

    # Attach the public featured image URL.
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
    print(f"Text: {post.get('text', post_text)}")
    print(f"Queued for: {post.get('dueAt') or 'Not specified'}")
    print(f"Attached assets: {post.get('assets', [])}")

    return post


# --------------------------------------------------
# WORDPRESS
# --------------------------------------------------

def fetch_wordpress_posts():
    """Fetch the latest WordPress articles."""

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

    articles = data[:MAX_POSTS]

    print(f"WordPress returned {len(data)} posts.")
    print(f"Will process up to {len(articles)} latest posts.")

    return articles


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():
    if not BUFFER_API_KEY:
        raise SystemExit(
            "Please configure BUFFER_API_KEY in your .env file."
        )

    if not BUFFER_CHANNEL_ID:
        raise SystemExit(
            "Please configure BUFFER_BLUESKY_CHANNEL_ID "
            "in your .env file."
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
        print(
            f"\n[{index}/{len(articles)}] "
            "Publishing Bluesky article..."
        )

        try:
            publish_to_bluesky(article)
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

    print("\n========== BLUESKY SUMMARY ==========")
    print(f"Total processed: {len(articles)}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"Downloaded images folder: {IMAGE_FOLDER}")


if __name__ == "__main__":
    main()
