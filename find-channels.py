
import os
import json
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------
# CONFIGURATION
# --------------------------------------------------

BUFFER_API_URL = "https://api.buffer.com"
BUFFER_API_KEY = os.getenv("BUFFER_API_KEY", "").strip()

if not BUFFER_API_KEY:
    sys.exit("ERROR: BUFFER_API_KEY is missing from your .env file.")

HEADERS = {
    "Authorization": f"Bearer {BUFFER_API_KEY}",
    "Content-Type": "application/json",
}


# --------------------------------------------------
# BUFFER GRAPHQL REQUEST
# --------------------------------------------------

def buffer_request(query):
    try:
        response = requests.post(
            BUFFER_API_URL,
            headers=HEADERS,
            json={"query": query},
            timeout=30,
        )

        response.raise_for_status()
        result = response.json()

        # GraphQL can return errors even with HTTP 200.
        if result.get("errors"):
            messages = [
                error.get("message", str(error))
                for error in result["errors"]
            ]
            raise RuntimeError("; ".join(messages))

        return result.get("data", {})

    except requests.RequestException as exc:
        raise RuntimeError(f"Buffer API request failed: {exc}") from exc


# --------------------------------------------------
# FETCH ORGANIZATIONS
# --------------------------------------------------

def get_organizations():
    query = """
    query GetOrganizations {
        account {
            organizations {
                id
                name
                ownerEmail
            }
        }
    }
    """

    data = buffer_request(query)
    account = data.get("account") or {}

    return account.get("organizations") or []


# --------------------------------------------------
# FETCH CHANNELS FOR AN ORGANIZATION
# --------------------------------------------------

def get_channels(organization_id):
    # GraphQL variables avoid inserting IDs directly into query text.
    query = """
    query GetChannels($organizationId: OrganizationId!) {
        channels(input: {
            organizationId: $organizationId
        }) {
            id
            name
            displayName
            service
            descriptor
            externalLink
            avatar
            isDisconnected
            isLocked
            isQueuePaused
        }
    }
    """

    # If your API schema expects a different ID scalar name,
    # check the Buffer API Explorer and adjust the variable type.
    try:
        response = requests.post(
            BUFFER_API_URL,
            headers=HEADERS,
            json={
                "query": query,
                "variables": {
                    "organizationId": organization_id
                },
            },
            timeout=30,
        )

        response.raise_for_status()
        result = response.json()

        if result.get("errors"):
            messages = [
                error.get("message", str(error))
                for error in result["errors"]
            ]
            raise RuntimeError("; ".join(messages))

        data = result.get("data") or {}
        return data.get("channels") or []

    except requests.RequestException as exc:
        raise RuntimeError(f"Channel request failed: {exc}") from exc


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():
    print("\nFetching Buffer organizations...\n")

    organizations = get_organizations()

    if not organizations:
        print("No organizations found for this API key.")
        return

    all_results = []

    for organization in organizations:
        org_id = organization.get("id")
        org_name = organization.get("name", "Unnamed organization")

        print("=" * 70)
        print(f"Organization: {org_name}")
        print(f"Organization ID: {org_id}")
        print("=" * 70)

        channels = get_channels(org_id)

        organization_result = {
            "organization_id": org_id,
            "organization_name": org_name,
            "owner_email": organization.get("ownerEmail"),
            "channels": channels,
        }
        all_results.append(organization_result)

        if not channels:
            print("  No channels found.\n")
            continue

        for index, channel in enumerate(channels, start=1):
            print(f"\n  Channel #{index}")
            print(f"  ID:           {channel.get('id')}")
            print(f"  Name:         {channel.get('name')}")
            print(f"  Display Name: {channel.get('displayName')}")
            print(f"  Platform:     {channel.get('service')}")
            print(f"  Descriptor:   {channel.get('descriptor')}")
            print(f"  Profile URL:  {channel.get('externalLink')}")
            print(f"  Disconnected: {channel.get('isDisconnected')}")
            print(f"  Locked:       {channel.get('isLocked')}")
            print(f"  Queue Paused: {channel.get('isQueuePaused')}")

        print()

    # Save the complete response to a JSON file.
    output_file = "buffer_organizations_channels.json"

    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(all_results, file, indent=2, ensure_ascii=False)

    print(f"Finished. Results saved to: {output_file}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"\nERROR: {exc}")
        sys.exit(1)
