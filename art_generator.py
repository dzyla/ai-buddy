#!/usr/bin/env python3
import random

def generate_ascii_art(width=80, height=15):
    """Generates a simple, random ASCII art pattern."""
    lines = []
    symbols = ['*', '#', '@', '-', '+', '=' , ' ', '/']
    
    for _ in range(height):
        line = []
        for _ in range(width):
            # Introduce some randomness to simulate complexity
            if random.random() < 0.1:
                line.append(random.choice(['.', ' ', ' '])) # Background space
            else:
                line.append(random.choice(symbols))
        lines.append("".join(line))
    
    return "\n".join(lines)

def generate_banner_art(text="CLI Art"):
    """Generates a stylized banner using block characters."""
    padding = 4
    width = len(text) + 2 * padding
    
    lines = []
    
    # Top border
    top_border = "=" * width
    lines.append(top_border)
    
    # Art line 1
    art_line1 = f"*{' ' * (width-4)}*"
    lines.append(art_line1)
    
    # Main text line
    main_text = f"| {'-' * (width - 4)} |"
    lines.append(main_text)
    
    # Text placement (padded)
    text_line = f"| {text:^{width - 4}} |"
    lines.append(text_line)
    
    # Bottom border
    bottom_border = "=" * width
    lines.append(bottom_border)
    
    return "\n".join(lines)

if __name__ == "__main__":
    # Generate a combination of two styles for a good visual output
    print("=" * 80)
    print("✨ ASCII Art Generator v1.0 ✨")
    print("=" * 80)
    print("\n--- Simple Pattern Art ---")
    print(generate_ascii_art(width=80, height=5))
    
    print("\n\n--- Stylized Banner Art ---")
    banner = generate_banner_art("CLI ART")
    print(banner)