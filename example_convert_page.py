#!/usr/bin/env python3
"""
Example script to demonstrate the usage of the wikitext_converter module.
This script reads 'page_content.txt', converts it to Markdown, and saves the result to 'page_content.md'.
"""

from core.wikitext_converter import convert_to_markdown


def main():
    """Main function to perform the conversion."""
    input_filename = "page_content.txt"
    output_filename = "page_content.md"

    try:
        # Read the raw wikitext content from the file
        with open(input_filename, 'r', encoding='utf-8') as f:
            wikitext_content = f.read()

        # Convert the wikitext to Markdown
        markdown_content = convert_to_markdown(wikitext_content)

        # Write the converted Markdown content to a new file
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        print(f"Conversion successful! Markdown content saved to '{output_filename}'.")

    except FileNotFoundError:
        print(f"Error: Input file '{input_filename}' not found.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    main()