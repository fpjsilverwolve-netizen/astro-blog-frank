import os
import re
import requests
from datetime import datetime

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

OUTPUT_DIR = "posts"
IMAGE_DIR = os.path.join(OUTPUT_DIR, "images")

def setup_directories():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(IMAGE_DIR, exist_ok=True)

def query_database():
    """查询 Notion 数据库中已发布的文章"""
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    # 可根据需要在 Notion 数据库中添加 Status 字段过滤，比如 Status == Published
    payload = {
        "filter": {
            "property": "Status",
            "status": {
                "equals": "Published"
            }
        }
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    if response.status_code != 200:
        # 如果没有 Status 字段，回退到查询全部页面
        response = requests.post(url, headers=HEADERS, json={})
    return response.json().get("results", [])

def get_block_children(block_id):
    """递归/分页获取 Page 下的所有 Block 节点"""
    url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
    blocks = []
    while url:
        res = requests.get(url, headers=HEADERS).json()
        blocks.extend(res.get("results", []))
        if res.get("has_more"):
            url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100&start_cursor={res.get('next_cursor')}"
        else:
            url = None
    return blocks

def download_image(url, filename):
    """下载图片到本地避免 Notion 签名链接失效"""
    try:
        res = requests.get(url, stream=True)
        if res.status_code == 200:
            filepath = os.path.join(IMAGE_DIR, filename)
            with open(filepath, 'wb') as f:
                for chunk in res.iter_content(1024):
                    f.write(chunk)
            return f"./images/{filename}"
    except Exception as e:
        print(f"Failed to download image {url}: {e}")
    return url

def rich_text_to_md(rich_text_list):
    """处理粗体、斜体、代码、链接等富文本"""
    md = ""
    for text in rich_text_list:
        content = text.get("plain_text", "")
        annotations = text.get("annotations", {})
        link = text.get("href")

        if annotations.get("code"):
            content = f"`{content}`"
        if annotations.get("bold"):
            content = f"**{content}**"
        if annotations.get("italic"):
            content = f"*{content}*"
        if annotations.get("strikethrough"):
            content = f"~~{content}~~"
        if link:
            content = f"[{content}]({link})"
        md += content
    return md

def block_to_markdown(block, page_id):
    """将 Notion Block 转换为 Markdown"""
    b_type = block.get("type")
    data = block.get(b_type, {})
    
    if b_type == "paragraph":
        return rich_text_to_md(data.get("rich_text", [])) + "\n\n"
    elif b_type in ["heading_1", "heading_2", "heading_3"]:
        level = "#" * int(b_type[-1])
        return f"{level} {rich_text_to_md(data.get('rich_text', []))}\n\n"
    elif b_type == "bulleted_list_item":
        return f"* {rich_text_to_md(data.get('rich_text', []))}\n"
    elif b_type == "numbered_list_item":
        return f"1. {rich_text_to_md(data.get('rich_text', []))}\n"
    elif b_type == "to_do":
        checked = "x" if data.get("checked") else " "
        return f"- [{checked}] {rich_text_to_md(data.get('rich_text', []))}\n"
    elif b_type == "code":
        lang = data.get("language", "")
        code_text = rich_text_to_md(data.get("rich_text", []))
        return f"```{lang}\n{code_text}\n```\n\n"
    elif b_type == "quote":
        return f"> {rich_text_to_md(data.get('rich_text', []))}\n\n"
    elif b_type == "image":
        img_type = data.get("type")
        img_url = data.get(img_type, {}).get("url", "")
        if img_url:
            img_name = f"{page_id}_{block.get('id')[:8]}.png"
            local_path = download_image(img_url, img_name)
            caption = rich_text_to_md(data.get("caption", []))
            return f"![{caption}]({local_path})\n\n"
    elif b_type == "divider":
        return "---\n\n"
    return ""

def extract_properties(page):
    """提取页面属性生成 Front Matter"""
    props = page.get("properties", {})
    
    # 提取标题
    title = "Untitled"
    for p in props.values():
        if p.get("type") == "title" and p.get("title"):
            title = p["title"][0].get("plain_text", "Untitled")
            break
            
    # 提取标签 (Multi-select)
    tags = []
    if "Tags" in props and props["Tags"].get("type") == "multi_select":
        tags = [t["name"] for t in props["Tags"]["multi_select"]]
        
    # 提取创建/发布日期
    date = page.get("created_time", "")[:10]
    if "Date" in props and props["Date"].get("type") == "date" and props["Date"]["date"]:
        date = props["Date"]["date"]["start"]

    # 提取 Slug（若无则用安全文件名）
    slug = re.sub(r'[\s/]+', '-', title.lower()).strip('-')
    if "Slug" in props and props["Slug"].get("type") == "rich_text" and props["Slug"]["rich_text"]:
        slug = props["Slug"]["rich_text"][0].get("plain_text", slug)

    front_matter = f"""---
title: "{title}"
date: {date}
tags: {tags}
slug: "{slug}"
---

"""
    return front_matter, slug

def main():
    setup_directories()
    pages = query_database()
    print(f"Found {len(pages)} articles to process.")

    for page in pages:
        page_id = page["id"]
        front_matter, slug = extract_properties(page)
        
        blocks = get_block_children(page_id)
        content_md = ""
        for b in blocks:
            content_md += block_to_markdown(b, page_id)

        filename = f"{slug}.md"
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(front_matter + content_md)
        print(f"Successfully synced: {filename}")

if __name__ == "__main__":
    main()
