import json
import os
import re
from datetime import datetime, timezone


def parse_iso_to_local(ts: str) -> datetime:
    fmts = [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in fmts:
        try:
            dt_utc = datetime.strptime(ts, fmt)
            dt_local = dt_utc.replace(tzinfo=timezone.utc).astimezone(tz=None)
            return dt_local.replace(tzinfo=None)
        except ValueError:
            pass
    return None


def format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def is_probably_system_id(sender_id: str, conversation_id: str) -> bool:
    if not sender_id:
        return True
    sender_id_lower = sender_id.lower()
    conv_id_lower = (conversation_id or "").lower()

    if sender_id_lower == conv_id_lower:
        return True
    if sender_id_lower.startswith("19:") and "@thread" in sender_id_lower:
        return True
    return False


def main():
    export_dir = "."
    msg_file = os.path.join(export_dir, "messages.json")
    media_dir = os.path.join(export_dir, "media")

    if not os.path.exists(msg_file):
        print("messages.json not found.")
        return

    with open(msg_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    user_id = data.get("userId", "")

    convs = data.get("conversations", [])
    if not convs:
        print("No conversations found.")
        return

    for i, c in enumerate(convs):
        conv_name = c.get("displayName") or "Unnamed"
        thread_props = c.get("threadProperties") or {}
        raw_members = thread_props.get("members")
        members_list = []
        if raw_members:
            if isinstance(raw_members, str):
                try:
                    members_list = json.loads(raw_members)
                except json.JSONDecodeError:
                    pass
            elif isinstance(raw_members, list):
                members_list = raw_members
        members_str = ", ".join(members_list) if members_list else "No members listed"
        print(f"[{i}] {conv_name} | Members: {members_str}")

    choice = input(f"Enter conversation index (0..{len(convs)-1}): ")
    try:
        choice = int(choice)
        conversation = convs[choice]
    except (ValueError, IndexError):
        print("Invalid choice.")
        return

    message_list = conversation.get("MessageList", [])
    if not message_list:
        print("No messages found.")
        return

    conversation_id = conversation.get("id", "")
    chat_name = conversation.get("displayName", f"chat_{choice}")

    media_files = {}
    if os.path.isdir(media_dir):
        for fname in os.listdir(media_dir):
            base = fname.split(".", 1)[0]
            media_files[base] = fname

    quote_pattern = re.compile(
        r'<quote.*?authorname="(.*?)".*?>(.*?)</quote>', re.DOTALL
    )

    def convert_quote(m):
        author = m.group(1)
        text = m.group(2)
        text = re.sub(
            r"<legacyquote>.*?</legacyquote>", "", text, flags=re.DOTALL
        ).strip()
        return f"> **Quoted from {author}**\n> {text.replace('\n', '\n> ')}"

    partlist_pattern = re.compile(r"<partlist.*?>(.*?)</partlist>", re.DOTALL)
    part_pattern = re.compile(
        r'<part.*?identity="(.*?)".*?<name>(.*?)</name>.*?<duration>(.*?)</duration>.*?</part>',
        re.DOTALL,
    )

    def convert_partlist(m):
        inside = m.group(1)
        parts = part_pattern.findall(inside)
        lines = ["**Call ended**"]
        for identity, name, duration in parts:
            lines.append(f"- {name} ({duration}s)")
        return "\n".join(lines)

    emoji_pattern = re.compile(r'<ss.*?utf="(.*?)".*?>.*?</ss>', re.DOTALL)

    def convert_emoji(m):
        return m.group(1)

    def convert_bold(m):
        return f"**{m.group(1)}**"

    def convert_italic(m):
        return f"*{m.group(1)}*"

    def convert_strikethrough(m):
        return f"~~{m.group(1)}~~"

    addmember_pattern = re.compile(r"<addmember>(.*?)</addmember>", re.DOTALL)

    def convert_addmember(m):
        inside = m.group(1)

        initiator = re.search(r"<initiator>(.*?)</initiator>", inside)
        eventtime = re.search(r"<eventtime>(.*?)</eventtime>", inside)
        rosterver = re.search(r"<rosterVersion>(.*?)</rosterVersion>", inside)
        targets = re.findall(r"<target>(.*?)</target>", inside)

        initiator_val = initiator.group(1) if initiator else "Unknown"
        eventtime_val = eventtime.group(1) if eventtime else "N/A"
        roster_val = rosterver.group(1) if rosterver else "N/A"
        target_str = ", ".join(targets) if targets else "No targets"

        lines = [
            "**AddMember Event**",
            f"- Time: {eventtime_val}",
            f"- Initiator: {initiator_val}",
            f"- Targets: {target_str}",
            f"- RosterVersion: {roster_val}",
        ]
        return "\n".join(lines)

    def convert_rich_text(content: str) -> str:
        content = quote_pattern.sub(convert_quote, content)
        content = partlist_pattern.sub(convert_partlist, content)
        content = addmember_pattern.sub(convert_addmember, content)
        content = emoji_pattern.sub(convert_emoji, content)

        content = re.sub(r"<b[^>]*>(.*?)</b>", convert_bold, content, flags=re.DOTALL)
        content = re.sub(r"<i[^>]*>(.*?)</i>", convert_italic, content, flags=re.DOTALL)
        content = re.sub(
            r"<s[^>]*>(.*?)</s>", convert_strikethrough, content, flags=re.DOTALL
        )

        return content

    def doc_id_to_md_link(doc_id: str) -> str:
        if doc_id not in media_files:
            return f"[{doc_id}](media/{doc_id})"
        fname = media_files[doc_id]
        lower_ext = os.path.splitext(fname)[1].lower()
        image_exts = [".png", ".jpg", ".jpeg", ".gif", ".webp"]
        if lower_ext in image_exts:
            return f"![{fname}](media/{fname})"
        else:
            return f"[{fname}](media/{fname})"

    processed_msgs = []
    for msg in message_list:
        raw_ts = msg.get("originalarrivaltime", "")
        dt_local = parse_iso_to_local(raw_ts)

        sender_id = msg.get("from", "")
        sender_disp = msg.get("displayName") or sender_id
        content = msg.get("content", "") or ""

        if sender_id == user_id:
            sender_disp = "You"
        else:
            if is_probably_system_id(sender_id, conversation_id):
                sender_disp = "System"

        if 'doc_id="' in content:
            start = content.find('doc_id="') + len('doc_id="')
            end = content.find('"', start)
            if end != -1:
                found_doc_id = content[start:end]
                content = doc_id_to_md_link(found_doc_id)

        content = convert_rich_text(content)

        processed_msgs.append((dt_local, sender_id, sender_disp, content))

    processed_msgs.sort(key=lambda x: x[0] if x[0] else datetime.min)

    grouped = []
    current_sender = None
    current_sender_name = None
    current_block = []

    for msg_dt, s_id, s_name, content in processed_msgs:
        if s_id != current_sender:
            if current_block:
                grouped.append((current_sender_name, current_block))
            current_sender = s_id
            current_sender_name = s_name
            current_block = []
        current_block.append((msg_dt, content))

    if current_block:
        grouped.append((current_sender_name, current_block))

    MERGE_SECONDS = 30
    merged_grouped = []

    for sender_name, block in grouped:
        if not block:
            continue

        merged_block = []
        cur_dt, cur_content = block[0]
        sub_msgs = [cur_content]

        for i in range(1, len(block)):
            next_dt, next_content = block[i]
            if not (cur_dt and next_dt):
                merged_block.append((cur_dt, sub_msgs))
                cur_dt, cur_content = next_dt, next_content
                sub_msgs = [cur_content]
                continue

            delta = (next_dt - cur_dt).total_seconds()
            if delta < MERGE_SECONDS:
                sub_msgs.append(next_content)
            else:
                merged_block.append((cur_dt, sub_msgs))
                cur_dt, cur_content = next_dt, next_content
                sub_msgs = [cur_content]

        merged_block.append((cur_dt, sub_msgs))
        merged_grouped.append((sender_name, merged_block))

    out_name = f"{chat_name.replace(' ', '_')}.md"
    with open(out_name, "w", encoding="utf-8") as out:
        out.write(f"# Chat Export - {chat_name}\n\n")

        for idx, (sender_name, block) in enumerate(merged_grouped):
            for dt_local, sub_contents in block:
                if dt_local:
                    time_str = format_dt(dt_local)
                    out.write(f"**{sender_name}  {time_str}:**\n")
                else:
                    out.write(f"**{sender_name}  [No Timestamp]:**\n")

                for text in sub_contents:
                    for line in text.split("\n"):
                        out.write(f"  {line}\n")
                out.write("\n")

            if idx < len(merged_grouped) - 1:
                out.write("\n")

    print(f"Exported to {out_name}")


if __name__ == "__main__":
    main()
