#!/usr/bin/env python3
"""
Parse a LinkedIn feed HTML file and extract readable post information.

Double-click to launch GUI mode, or use CLI:
  python3 parse_linkedin_feed.py delete.txt [--json] [--csv]
"""

import re
import sys
import json
import csv
import io
import os
from html.parser import HTMLParser


class LinkedInFeedParser:
    def __init__(self, html: str):
        self.html = html
        self.posts = []

    def _strip_html(self, text: str) -> str:
        """Remove HTML tags and clean up text."""
        # Remove HTML comments
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
        # Replace <br> and <br/> with newlines
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        # Remove all other HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Decode common HTML entities
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
        # Collapse whitespace on each line, preserve newlines
        lines = text.split('\n')
        lines = [' '.join(line.split()) for line in lines]
        # Remove empty lines but keep intentional breaks
        cleaned = []
        prev_empty = False
        for line in lines:
            if line.strip():
                cleaned.append(line.strip())
                prev_empty = False
            elif not prev_empty:
                cleaned.append('')
                prev_empty = True
        return '\n'.join(cleaned).strip()

    def _extract_between(self, text: str, start_marker: str, end_marker: str) -> str:
        """Extract text between two markers."""
        idx = text.find(start_marker)
        if idx == -1:
            return ''
        idx += len(start_marker)
        end_idx = text.find(end_marker, idx)
        if end_idx == -1:
            return ''
        return text[idx:end_idx]

    def parse(self):
        """Parse the HTML and extract all feed posts."""
        # Split by feed post boundaries
        # Each post starts with feed-shared-update-v2 div
        post_pattern = r'<div\s+class="feed-shared-update-v2\s+feed-shared-update-v2--minimal-padding'
        post_starts = [m.start() for m in re.finditer(post_pattern, self.html)]

        if not post_starts:
            print("No posts found with primary pattern, trying alternate...")
            return self.posts

        # Create post chunks
        chunks = []
        for i, start in enumerate(post_starts):
            end = post_starts[i + 1] if i + 1 < len(post_starts) else len(self.html)
            chunks.append(self.html[start:end])

        for i, chunk in enumerate(chunks):
            post = self._parse_post(chunk, i + 1)
            if post and (post.get('author') or post.get('content')):
                self.posts.append(post)

        return self.posts

    def _parse_post(self, chunk: str, post_num: int) -> dict:
        """Parse a single post chunk."""
        post = {'post_number': post_num}

        # --- Author name ---
        # Look for the actor title with the actual name
        author_match = re.search(
            r'<span\s+aria-hidden="true"><!---->(.+?)<!----></span>\s*'
            r'<span\s+class="visually-hidden">',
            chunk
        )
        if author_match:
            name = self._strip_html(author_match.group(1)).strip()
            # Sometimes the name appears duplicated; take first clean version
            if name:
                post['author'] = name

        # --- Author headline/description ---
        desc_match = re.search(
            r'update-components-actor__description.*?'
            r'<span\s+aria-hidden="true"><!---->(.+?)<!----></span>',
            chunk, re.DOTALL
        )
        if desc_match:
            post['headline'] = self._strip_html(desc_match.group(1)).strip()

        # --- Timestamp ---
        time_match = re.search(
            r'update-components-actor__sub-description.*?'
            r'<span\s+class="visually-hidden"><!---->(.+?)<!----></span>',
            chunk, re.DOTALL
        )
        if time_match:
            ts = self._strip_html(time_match.group(1)).strip()
            # Clean up the timestamp
            ts = re.sub(r'\s+', ' ', ts)
            post['timestamp'] = ts

        # --- Post content ---
        content_match = re.search(
            r'update-components-text\s+relative\s+update-components-update-v2__commentary.*?'
            r'<span\s+class="break-words.*?">(.*?)</span>\s*</div>',
            chunk, re.DOTALL
        )
        if content_match:
            raw_content = content_match.group(1)
            # Extract hashtags before stripping
            hashtags = re.findall(r'#(\w+)', raw_content)
            content = self._strip_html(raw_content)
            # Clean up hashtag formatting
            content = re.sub(r'hashtag\s*', '', content)
            post['content'] = content
            if hashtags:
                post['hashtags'] = ['#' + h for h in hashtags]

        # --- Reactions count ---
        reactions_match = re.search(
            r'aria-label="([\d,]+)\s+reactions?"',
            chunk
        )
        if reactions_match:
            post['reactions'] = reactions_match.group(1)
        else:
            # Sometimes it's like "Tyler Reynolds and 113 others"
            reactions_match2 = re.search(
                r'aria-label="(.+?\s+and\s+[\d,]+\s+others?)"',
                chunk
            )
            if reactions_match2:
                post['reactions'] = reactions_match2.group(1)

        # --- Comments count ---
        comments_match = re.search(
            r'aria-label="(\d+)\s+comments?\s+on\s+(.+?)\'s\s+post"',
            chunk
        )
        if comments_match:
            post['comments'] = comments_match.group(1)

        # --- Reposts count ---
        reposts_match = re.search(
            r'aria-label="(\d+)\s+reposts?\s+of\s+',
            chunk
        )
        if reposts_match:
            post['reposts'] = reposts_match.group(1)

        # --- Shared article/link ---
        article_match = re.search(
            r'update-components-article__title.*?<span[^>]*><!---->(.+?)<!----></span>',
            chunk, re.DOTALL
        )
        if article_match:
            post['shared_article'] = self._strip_html(article_match.group(1)).strip()

        # --- Context (e.g., "X likes this", "X commented on this", "Suggested") ---
        context_match = re.search(
            r'update-components-header__text-view.*?<span[^>]*><!---->(.+?)<!----></span>',
            chunk, re.DOTALL
        )
        if context_match:
            ctx = self._strip_html(context_match.group(1)).strip()
            if ctx:
                post['context'] = ctx

        # --- Reaction types ---
        reaction_types = re.findall(
            r'data-test-reactions-icon-type="(\w+)"',
            chunk
        )
        if reaction_types:
            type_map = {
                'LIKE': 'like', 'PRAISE': 'celebrate', 'EMPATHY': 'love',
                'APPRECIATION': 'support', 'INTEREST': 'insightful',
                'ENTERTAINMENT': 'funny'
            }
            post['reaction_types'] = list(dict.fromkeys(
                type_map.get(r, r.lower()) for r in reaction_types
            ))

        # --- Comments ---
        post['comment_list'] = self._parse_comments(chunk)

        return post

    def _parse_comments(self, chunk: str) -> list:
        """Extract visible comments from a post chunk."""
        comments = []
        # Find each comment block by the commenter name pattern
        comment_blocks = list(re.finditer(
            r'comments-comment-meta__description-title">\s*<!---->(.+?)<!---->',
            chunk
        ))
        # Find each comment text block
        comment_texts = list(re.finditer(
            r'comments-comment-item__main-content.*?dir="ltr">\s*(.*?)\s*</div>',
            chunk, re.DOTALL
        ))
        # Find comment timestamps
        comment_times = list(re.finditer(
            r'<time\s+class="comments-comment-meta__data">\s*(\S+)\s*</time>',
            chunk
        ))
        for i in range(min(len(comment_blocks), len(comment_texts))):
            name = self._strip_html(comment_blocks[i].group(1)).strip()
            text = self._strip_html(comment_texts[i].group(1)).strip()
            time = comment_times[i].group(1).strip() if i < len(comment_times) else ''
            if name and text:
                comment = {'author': name, 'text': text}
                if time:
                    comment['time'] = time
                comments.append(comment)
        return comments


def format_post_readable(post: dict) -> str:
    """Format a single post for readable terminal output."""
    lines = []
    lines.append(f"{'='*70}")
    lines.append(f"POST #{post.get('post_number', '?')}")
    lines.append(f"{'='*70}")

    if post.get('context'):
        lines.append(f"  [{post['context']}]")

    if post.get('author'):
        lines.append(f"  Author:    {post['author']}")
    if post.get('headline'):
        lines.append(f"  Headline:  {post['headline']}")
    if post.get('timestamp'):
        lines.append(f"  Posted:    {post['timestamp']}")

    lines.append(f"  {'-'*66}")

    if post.get('content'):
        # Indent content
        for line in post['content'].split('\n'):
            lines.append(f"  {line}")
    else:
        lines.append("  [No text content / media only]")

    if post.get('shared_article'):
        lines.append(f"\n  Shared: {post['shared_article']}")

    lines.append(f"  {'-'*66}")

    engagement = []
    if post.get('reactions'):
        reactions_str = post['reactions']
        if post.get('reaction_types'):
            reactions_str += f" ({', '.join(post['reaction_types'])})"
        engagement.append(f"Reactions: {reactions_str}")
    if post.get('comments'):
        engagement.append(f"Comments: {post['comments']}")
    if post.get('reposts'):
        engagement.append(f"Reposts: {post['reposts']}")

    if engagement:
        lines.append(f"  {' | '.join(engagement)}")

    if post.get('hashtags'):
        lines.append(f"  Tags: {' '.join(post['hashtags'])}")

    if post.get('comment_list'):
        lines.append(f"\n  Comments:")
        for c in post['comment_list']:
            time_str = f" ({c['time']})" if c.get('time') else ''
            lines.append(f"    > {c['author']}{time_str}: {c['text']}")

    lines.append('')
    return '\n'.join(lines)


def _format_posts(posts, fmt='text'):
    """Format parsed posts into the requested output format."""
    if fmt == 'json':
        return json.dumps(posts, indent=2, ensure_ascii=False)
    elif fmt == 'csv':
        buf = io.StringIO()
        if posts:
            fields = ['post_number', 'author', 'headline', 'timestamp',
                       'content', 'reactions', 'comments', 'reposts',
                       'hashtags', 'shared_article', 'context', 'reaction_types',
                       'comment_list']
            writer = csv.DictWriter(buf, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            for post in posts:
                row = dict(post)
                if 'hashtags' in row:
                    row['hashtags'] = ' '.join(row['hashtags'])
                if 'reaction_types' in row:
                    row['reaction_types'] = ', '.join(row['reaction_types'])
                if 'content' in row and row['content']:
                    row['content'] = row['content'].replace('\n', ' | ')
                if 'comment_list' in row:
                    row['comment_list'] = ' // '.join(
                        f"{c['author']}: {c['text']}" for c in row['comment_list']
                    )
            writer.writerow(row)
        return buf.getvalue()
    else:
        parts = []
        parts.append(f"\nLINKEDIN FEED SUMMARY")
        parts.append(f"{'='*70}")
        parts.append(f"Total posts found: {len(posts)}\n")
        for post in posts:
            parts.append(format_post_readable(post))
        return '\n'.join(parts)


def parse_and_format(filepath, fmt='text'):
    """Parse a LinkedIn HTML file and return formatted output."""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        html = f.read()
    feed_parser = LinkedInFeedParser(html)
    posts = feed_parser.parse()
    return posts, _format_posts(posts, fmt)


def parse_from_html_string(html, fmt='text'):
    """Parse LinkedIn HTML from a string and return formatted output."""
    feed_parser = LinkedInFeedParser(html)
    posts = feed_parser.parse()
    return posts, _format_posts(posts, fmt)


def run_gui():
    """Launch a simple tkinter GUI for parsing LinkedIn HTML."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    root = tk.Tk()
    root.title("LinkedIn Feed Parser")
    root.geometry("900x700")
    root.minsize(700, 500)

    # Track which view is showing
    showing_results = False
    raw_html = ""

    # --- Top bar: format selector + load file button ---
    top = tk.Frame(root, padx=10, pady=10)
    top.pack(fill=tk.X)

    tk.Label(top, text="Format:").pack(side=tk.LEFT)
    fmt_var = tk.StringVar(value="text")
    for label, val in [("Text", "text"), ("JSON", "json"), ("CSV", "csv")]:
        tk.Radiobutton(top, text=label, variable=fmt_var, value=val).pack(side=tk.LEFT)

    def load_file():
        start_dir = os.path.dirname(os.path.abspath(__file__))
        path = filedialog.askopenfilename(
            title="Select LinkedIn HTML file",
            initialdir=start_dir,
            filetypes=[("HTML files", "*.html *.htm *.txt"), ("All files", "*.*")]
        )
        if path:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            text_area.delete("1.0", tk.END)
            text_area.insert(tk.END, content)
            status_var.set(f"Loaded {os.path.basename(path)} — click Parse.")

    def do_paste():
        clipboard = None
        # Try CLIPBOARD first (Ctrl+C), then PRIMARY (mouse selection)
        for sel in ("CLIPBOARD", "PRIMARY"):
            try:
                clipboard = root.selection_get(selection=sel)
                if clipboard:
                    break
            except tk.TclError:
                continue
        # Also try xclip/xsel as a fallback
        if not clipboard:
            import subprocess
            for cmd in (["xclip", "-selection", "clipboard", "-o"],
                        ["xsel", "--clipboard", "--output"]):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                    if result.returncode == 0 and result.stdout:
                        clipboard = result.stdout
                        break
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
        if not clipboard:
            messagebox.showinfo("Clipboard Empty", "Nothing on the clipboard to paste.\n\n"
                                "Try: copy text first with Ctrl+C, then click Paste.")
            return
        text_area.delete("1.0", tk.END)
        text_area.insert(tk.END, clipboard)
        text_area.see("1.0")
        status_var.set(f"Pasted {len(clipboard):,} characters — click Parse.")

    tk.Button(top, text="Load File...", command=load_file).pack(side=tk.RIGHT)
    tk.Button(top, text="Paste", command=do_paste, bg="#2e7d32", fg="white",
              font=("sans-serif", 10, "bold"), padx=10).pack(side=tk.RIGHT, padx=(0, 5))

    # --- Main text area: paste here OR view results ---
    text_area = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("monospace", 10))
    text_area.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))
    text_area.insert(tk.END, "Click the Paste button (or Ctrl+V) then click Parse.\n\n"
                     "(Or use Load File to pick an HTML file.)")

    # Bind Ctrl+V explicitly since tkinter on Linux can be flaky with it
    def on_ctrl_v(event):
        do_paste()
        return "break"
    text_area.bind("<Control-v>", on_ctrl_v)
    text_area.bind("<Control-V>", on_ctrl_v)

    # --- Bottom bar: status + buttons ---
    bottom = tk.Frame(root, padx=10, pady=10)
    bottom.pack(fill=tk.X)

    status_var = tk.StringVar(value="Paste HTML and click Parse.")
    tk.Label(bottom, textvariable=status_var, anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def do_parse():
        nonlocal showing_results, raw_html

        html = text_area.get("1.0", tk.END).strip()
        if not html or html.startswith("Paste your LinkedIn HTML here"):
            messagebox.showinfo("Nothing to parse", "Paste HTML content first.")
            return

        # Save the raw HTML so we can go back
        raw_html = html
        status_var.set("Parsing...")
        root.update()

        try:
            posts, output = parse_from_html_string(html, fmt_var.get())
            text_area.delete("1.0", tk.END)
            text_area.insert(tk.END, output)
            text_area.see("1.0")
            showing_results = True
            back_btn.pack(side=tk.RIGHT, padx=(5, 0))
            status_var.set(f"Done — {len(posts)} posts found.")
        except Exception as e:
            messagebox.showerror("Parse Error", str(e))
            status_var.set("Error during parsing.")

    def do_back():
        nonlocal showing_results
        text_area.delete("1.0", tk.END)
        text_area.insert(tk.END, raw_html)
        showing_results = False
        back_btn.pack_forget()
        status_var.set("Showing raw HTML. Click Parse again.")

    def do_save():
        content = text_area.get("1.0", tk.END).strip()
        if not content:
            messagebox.showinfo("Nothing to save", "Parse a file first.")
            return
        ext_map = {"text": ".txt", "json": ".json", "csv": ".csv"}
        ext = ext_map.get(fmt_var.get(), ".txt")
        start_dir = os.path.dirname(os.path.abspath(__file__))
        path = filedialog.asksaveasfilename(
            title="Save output",
            initialdir=start_dir,
            defaultextension=ext,
            filetypes=[(f"{ext.upper()} files", f"*{ext}"), ("All files", "*.*")]
        )
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            status_var.set(f"Saved to {path}")

    back_btn = tk.Button(bottom, text="Back to Input", command=do_back, padx=10)
    # back_btn is only packed after a parse

    def do_clear():
        nonlocal showing_results, raw_html
        text_area.delete("1.0", tk.END)
        raw_html = ""
        showing_results = False
        back_btn.pack_forget()
        status_var.set("Cleared. Paste HTML and click Parse.")

    def do_copy():
        content = text_area.get("1.0", tk.END).strip()
        if not content:
            return
        root.clipboard_clear()
        root.clipboard_append(content)
        status_var.set("Copied to clipboard.")

    tk.Button(bottom, text="Parse", command=do_parse, bg="#0a66c2", fg="white",
              font=("sans-serif", 11, "bold"), padx=15).pack(side=tk.RIGHT, padx=(5, 0))
    tk.Button(bottom, text="Copy", command=do_copy, padx=10).pack(side=tk.RIGHT)
    tk.Button(bottom, text="Save As...", command=do_save, padx=10).pack(side=tk.RIGHT)
    tk.Button(bottom, text="Clear", command=do_clear, padx=10).pack(side=tk.RIGHT)

    root.mainloop()


def main():
    # If no arguments, launch GUI mode
    if len(sys.argv) == 1:
        run_gui()
        return

    import argparse
    parser = argparse.ArgumentParser(description='Parse LinkedIn feed HTML into readable format')
    parser.add_argument('file', help='Path to the LinkedIn HTML file')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--csv', action='store_true', help='Output as CSV')
    parser.add_argument('--output', '-o', help='Write output to file instead of stdout')
    args = parser.parse_args()

    fmt = 'json' if args.json else ('csv' if args.csv else 'text')
    print(f"Reading {args.file}...", file=sys.stderr)
    posts, output = parse_and_format(args.file, fmt)
    print(f"Found {len(posts)} posts", file=sys.stderr)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"Output written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()
