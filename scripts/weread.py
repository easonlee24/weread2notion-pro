import argparse
import os
import requests

from notion_helper import NotionHelper
from weread_api import WeReadApi
from datetime import datetime

from utils import (
    get_callout,
    get_heading,
    get_number,
    get_number_from_result,
    get_quote,
    get_rich_text_from_result,
    get_table_of_contents,
)


# 返回微信阅读最新的划线信息，会做如下两个处理：
# a）如果划线在notion中存在，则设置blockid
# b）如果划线在notion存在，但微信阅读已经不存在了，则从notion中删除。
def get_bookmark_list(page_id, bookId):
    """获取我的划线"""
    filter = {
        "and": [
            {"property": "书籍", "relation": {"contains": page_id}},
            {"property": "blockId", "rich_text": {"is_not_empty": True}},
        ]
    }
    results = notion_helper.query_all_by_book(
        notion_helper.bookmark_database_id, filter
    )
    dict1 = {
        get_rich_text_from_result(x, "bookmarkId"): get_rich_text_from_result(
            x, "blockId"
        )
        for x in results
    }
    dict2 = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
    bookmarks = weread_api.get_bookmark_list(bookId)
    for i in bookmarks:
        if i.get("bookmarkId") in dict1:
            i["blockId"] = dict1.pop(i.get("bookmarkId"))
    # 删除微信阅读上已经取消的划线
    for blockId in dict1.values():
        notion_helper.delete_block(blockId)
        notion_helper.delete_block(dict2.get(blockId))
    return bookmarks


def get_review_list(page_id,bookId):
    """获取笔记"""
    filter = {
        "and": [
            {"property": "书籍", "relation": {"contains": page_id}},
            {"property": "blockId", "rich_text": {"is_not_empty": True}},
        ]
    }
    results = notion_helper.query_all_by_book(notion_helper.review_database_id, filter)
    dict1 = {
        get_rich_text_from_result(x, "reviewId"): get_rich_text_from_result(
            x, "blockId"
        )
        for x in results
    }
    dict2 = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
    reviews = weread_api.get_review_list(bookId)
    for i in reviews:
        if i.get("reviewId") in dict1:
            i["blockId"] = dict1.pop(i.get("reviewId"))
    # 删除微信阅读已经取消的笔记
    for blockId in dict1.values():
        notion_helper.delete_block(blockId)
        notion_helper.delete_block(dict2.get(blockId))
    return reviews


def check(bookId):
    """检查是否已经插入过"""
    filter = {"property": "BookId", "rich_text": {"equals": bookId}}
    response = notion_helper.query(
        database_id=notion_helper.book_database_id, filter=filter
    )
    if len(response["results"]) > 0:
        return response["results"][0]["id"]
    return None


def get_sort():
    """获取database中的最新时间"""
    filter = {"property": "Sort", "number": {"is_not_empty": True}}
    sorts = [
        {
            "property": "Sort",
            "direction": "descending",
        }
    ]
    response = notion_helper.query(
        database_id=notion_helper.book_database_id,
        filter=filter,
        sorts=sorts,
        page_size=1,
    )
    if len(response.get("results")) == 1:
        return response.get("results")[0].get("properties").get("Sort").get("number")
    return 0


def download_image(url, save_dir="cover"):
    # 确保目录存在，如果不存在则创建
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    # 获取文件名，使用 URL 最后一个 '/' 之后的字符串
    file_name = url.split("/")[-1] + ".jpg"
    save_path = os.path.join(save_dir, file_name)

    # 检查文件是否已经存在，如果存在则不进行下载
    if os.path.exists(save_path):
        print(f"File {file_name} already exists. Skipping download.")
        return save_path

    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(save_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=128):
                file.write(chunk)
        print(f"Image downloaded successfully to {save_path}")
    else:
        print(f"Failed to download image. Status code: {response.status_code}")
    return save_path


def sort_notes(page_id, chapter, bookmark_list):
    """对笔记和划线进行排序"""
    bookmark_list = sorted(
        bookmark_list,
        key=lambda x: (
            x.get("chapterUid", 1),
            0
            if (x.get("range", "") == "" or x.get("range").split("-")[0] == "")
            else int(x.get("range").split("-")[0]),
        ),
    )

    notes = []
    if chapter != None:
        filter = {"property": "书籍", "relation": {"contains": page_id}}

        # 已经插入到notion的chapter信息:
        results = notion_helper.query_all_by_book(
            notion_helper.chapter_database_id, filter
        )
        chapterUId2BlockIdMap = {
            get_number_from_result(x, "chapterUid"): get_rich_text_from_result(
                x, "blockId"
            )
            for x in results
        }
        blockId2ChapterMap = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
        # 按chapter聚合的划线和笔记
        notesByChapter = {}
        for data in bookmark_list:
            chapterUid = data.get("chapterUid", 1)
            if chapterUid not in notesByChapter:
                notesByChapter[chapterUid] = []
            notesByChapter[chapterUid].append(data)

        # 把章节信息也插入到notes中
        for key, value in notesByChapter.items():
            if key in chapter:
                if key in chapterUId2BlockIdMap:
                    chapter.get(key)["blockId"] = chapterUId2BlockIdMap.pop(key)
                notes.append(chapter.get(key))
            notes.extend(value)

        for blockId in chapterUId2BlockIdMap.values():
            notion_helper.delete_block(blockId)
            notion_helper.delete_block(blockId2ChapterMap.get(blockId))
    else:
        notes.extend(bookmark_list)
    return notes


def append_blocks(pageId, contents):
    print(f"笔记数{len(contents)}")
    before_block_id = ""
    block_children = notion_helper.get_block_children(pageId)
    # 先确保第一个block是toc，如果不是就插入一个
    if len(block_children) > 0 and block_children[0].get("type") == "table_of_contents":
        before_block_id = block_children[0].get("id")
    else:
        response = notion_helper.append_blocks(
            block_id=pageId, children=[get_table_of_contents()]
        )
        before_block_id = response.get("results")[0].get("id")
    blocks = []
    sub_contents = []
    l = []
    for content in contents:
        if len(blocks) == 100:
            results = append_blocks_to_notion(pageId, blocks, before_block_id, sub_contents)
            before_block_id = results[-1].get("blockId")
            l.extend(results)
            blocks.clear()
            sub_contents.clear()
            blocks.append(content_to_block(content))
            sub_contents.append(content)
        elif "blockId" in content:
            if len(blocks) > 0:
                l.extend(
                    append_blocks_to_notion(pageId, blocks, before_block_id, sub_contents)
                )
                blocks.clear()
                sub_contents.clear()
            before_block_id = content["blockId"]
        else:
            blocks.append(content_to_block(content))
            sub_contents.append(content)

    if len(blocks) > 0:
        l.extend(append_blocks_to_notion(pageId, blocks, before_block_id, sub_contents))
    for index, value in enumerate(l):
        print(f"正在插入第{index+1}条笔记，共{len(l)}条")
        if "bookmarkId" in value:
            notion_helper.insert_bookmark(pageId, value)
        elif "reviewId" in value:
            notion_helper.insert_review(pageId, value)
        else:
            notion_helper.insert_chapter(pageId, value)


def content_to_block(content):
    if "bookmarkId" in content:
        return get_callout(
            content.get("markText",""),
            content.get("style"),
            content.get("colorStyle"),
            content.get("reviewId"),
        )
    elif "reviewId" in content:
        return get_callout(
            content.get("content",""),
            content.get("style"),
            content.get("colorStyle"),
            content.get("reviewId"),
        )
    else:
        return get_heading(content.get("level"), content.get("title"))


def append_blocks_to_notion(id, blocks, after, contents):
    response = notion_helper.append_blocks_after(
        block_id=id, children=blocks, after=after
    )
    results = response.get("results")
    l = []
    for index, content in enumerate(contents):
        result = results[index]
        if content.get("abstract") != None and content.get("abstract") != "":
            notion_helper.append_blocks(
                block_id=result.get("id"), children=[get_quote(content.get("abstract"))]
            )
        content["blockId"] = result.get("id")
        l.append(content)
    return l


if __name__ == "__main__":
    current_time = datetime.now()
    print("开始同步笔记，当前时间: ", current_time)
    parser = argparse.ArgumentParser()
    options = parser.parse_args()
    branch = os.getenv("REF").split("/")[-1]
    repository =  os.getenv("REPOSITORY")
    weread_api = WeReadApi()
    notion_helper = NotionHelper()
    notion_books = notion_helper.get_all_book()
    books = weread_api.get_notebooklist()
    #print(notion_books)
    #print(books)
    #测试推送
    if books != None:
        for index, book in enumerate(books):
            bookId = book.get("bookId")
            title = book.get("book").get("title")
            sort = book.get("sort")
            if bookId not in notion_books:
                continue
            # 这个sort是啥意思？
            if sort == notion_books.get(bookId).get("Sort"):
                continue
            pageId = notion_books.get(bookId).get("pageId")
            print(f"正在同步《{title}》,一共{len(books)}本，当前是第{index+1}本。")
            chapter = weread_api.get_chapter_info(bookId)
            bookmark_list = get_bookmark_list(pageId, bookId)
            reviews = get_review_list(pageId,bookId)
            bookmark_list.extend(reviews)
            #会把原来页面的所有自动同步的block删除（包括笔记、划线、章节）
            content = sort_notes(pageId, chapter, bookmark_list)
            append_blocks(pageId, content)
            properties = {
                "Sort":get_number(sort)
            }
            notion_helper.update_book_page(page_id=pageId,properties=properties)
