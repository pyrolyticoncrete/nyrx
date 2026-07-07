-- SPDX-License-Identifier: AGPL-3.0-only

local CACHE_DIR = mp.get_opt("tracker_dir") or (os.getenv("HOME") .. "/.config/nyrx")
local LOG_FILE = CACHE_DIR .. "/tracker_v4.jsonl"

local NULL = {}

local function json_encode(v)
    if v == NULL then
        return "null"
    end
    local t = type(v)
    if t == "nil" then
        return "null"
    elseif t == "boolean" then
        return tostring(v)
    elseif t == "number" then
        if v == math.floor(v) then
            return string.format("%d", v)
        end
        return string.format("%g", v)
    elseif t == "string" then
        return '"' .. v:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n'):gsub('\r', '\\r'):gsub('\t', '\\t') .. '"'
    elseif t == "table" then
        local n = #v
        local is_array = true
        for k in pairs(v) do
            if type(k) ~= "number" or k < 1 or k > n then
                is_array = false
                break
            end
        end
        if is_array then
            local parts = {}
            for i = 1, n do
                parts[i] = json_encode(v[i])
            end
            return "[" .. table.concat(parts, ",") .. "]"
        else
            local parts = {}
            for k, vv in pairs(v) do
                parts[#parts + 1] = json_encode(k) .. ":" .. json_encode(vv)
            end
            return "{" .. table.concat(parts, ",") .. "}"
        end
    end
    return "null"
end

local function get_tracker_opt(key)
    local val = mp.get_opt("tracker_" .. key)
    if val and val ~= "" then
        return val
    end
    return nil
end

local total_play_time = 0
local segment_start = 0
local current_path = ""
local current_title = ""
local current_duration = 0
local current_yt_channel_url = ""

local last_icy_title = ""
local track_start_play_time = 0

local current_source = ""
local current_uploader_id = ""
local current_permalink = ""
local current_channel = ""

local function on_unpause()
    segment_start = os.time()
end

local function on_pause()
    if segment_start > 0 then
        total_play_time = total_play_time + (os.time() - segment_start)
        segment_start = 0
    end
end

local function flush_play_time()
    if segment_start > 0 then
        total_play_time = total_play_time + (os.time() - segment_start)
        segment_start = 0
    end
end

local function extract_station_info(path)
    local host = path:match("https?://([^/]+)")
    if not host then return "", "" end
    local mount = path:match("https?://[^/]+/(.*)")
    if mount then mount = mount:gsub("^[/\\]+", "") else mount = "" end
    return host, mount
end

local function write_entry(reason, title_override, watched_secs_override)
    if watched_secs_override and watched_secs_override <= 0 then return end
    if not watched_secs_override and total_play_time <= 0 then return end

    local yt_id = get_tracker_opt("yt_id")
    local media_type = get_tracker_opt("media_type")
    local season_str = get_tracker_opt("season_number")
    local episode_str = get_tracker_opt("episode_number")
    local resolved_title = title_override or get_tracker_opt("title") or current_title
    local watched = watched_secs_override or total_play_time

    local host, mount = NULL, NULL
    if current_source == "radio" then
        local h, m = extract_station_info(current_path)
        if h ~= "" then host = h end
        if m ~= "" then mount = m end
    end

    local entry = {
        _v = 4,
        ts = os.time(),
        source = current_source ~= "" and current_source or NULL,
        yt_id = yt_id or NULL,
        media_type = media_type or NULL,
        season_number = season_str and tonumber(season_str) or NULL,
        episode_number = episode_str and tonumber(episode_str) or NULL,
        title = resolved_title ~= "" and resolved_title or NULL,
        channel = current_channel ~= "" and current_channel or NULL,
        yt_channel_url = current_yt_channel_url ~= "" and current_yt_channel_url or NULL,
        uploader_id = current_uploader_id ~= "" and current_uploader_id or NULL,
        permalink = current_permalink ~= "" and current_permalink or NULL,
        station_host = host,
        station_mount = mount,
        watched_secs = math.floor(watched),
        duration_secs = math.floor(current_duration),
        reason = reason,
    }

    os.execute("mkdir -p " .. CACHE_DIR)
    local f = io.open(LOG_FILE, "a")
    if f then
        f:write(json_encode(entry), "\n")
        f:close()
    end
end

local function on_start_file()
    total_play_time = 0
    current_yt_channel_url = ""
    last_icy_title = ""
    track_start_play_time = 0
    current_source = get_tracker_opt("source") or ""
    current_uploader_id = get_tracker_opt("uploader_id") or ""
    current_permalink = get_tracker_opt("permalink") or ""
    current_channel = get_tracker_opt("channel") or ""
    local paused = mp.get_property_bool("pause")
    if paused then
        segment_start = 0
    else
        segment_start = os.time()
    end
end

mp.observe_property("pause", "bool", function(_, paused)
    if paused then on_pause() else on_unpause() end
end)

mp.observe_property("media-title", "string", function(_, val)
    if val and val ~= "" then current_title = val end
end)

mp.observe_property("metadata", "native", function(_, meta)
    if meta then
        if meta.channel_url then current_yt_channel_url = meta.channel_url end
        if meta["icy-title"] and meta["icy-title"] ~= "" then
            if last_icy_title ~= "" and meta["icy-title"] ~= last_icy_title then
                local was_playing = (segment_start > 0)
                flush_play_time()
                local track_time = total_play_time - track_start_play_time
                if track_time >= 3 then
                    write_entry("track_change", last_icy_title, track_time)
                end
                track_start_play_time = total_play_time
                if was_playing then segment_start = os.time() end
            elseif last_icy_title == "" then
                track_start_play_time = total_play_time
            end
            last_icy_title = meta["icy-title"]
            current_title = meta["icy-title"]
        end
    end
end)

mp.observe_property("path", "string", function(_, val)
    if val and val ~= "" then current_path = val end
end)

mp.observe_property("duration", "number", function(_, val)
    if val and val > 0 then current_duration = val end
end)

mp.register_event("start-file", on_start_file)

mp.register_event("end-file", function(event)
    flush_play_time()

    local reason = (event and event.reason) or "unknown"
    local last_track_time = total_play_time - track_start_play_time
    if track_start_play_time > 0 then
        write_entry(reason, nil, last_track_time)
    else
        write_entry(reason)
    end
end)
