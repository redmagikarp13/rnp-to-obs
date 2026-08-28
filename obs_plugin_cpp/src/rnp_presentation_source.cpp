#include "obs_headers.h"
#include "rnp_sources.h"
#include "rnp_session.h"
#include <string>
#include <vector>
#include <chrono>

struct rnp_presentation_source {
    obs_source_t *source;
    std::string room_url;
    uint32_t width;
    uint32_t height;
    std::vector<uint8_t> frame_buffer;
};

static const char *rnp_presentation_get_name(void *type_data) {
    (void)type_data;
    return "RNP - Apresentação / Slides (Tela Compartilhada)";
}

static void *rnp_presentation_create(obs_data_t *settings, obs_source_t *source) {
    struct rnp_presentation_source *context = new struct rnp_presentation_source();
    context->source = source;
    context->width = 1920;
    context->height = 1080;
    context->frame_buffer.resize(context->width * context->height * 4, 0);

    const char *url = obs_data_get_string(settings, "room_url");
    if (url && strlen(url) > 0) {
        context->room_url = url;
        RNPSession::getInstance().connectToRoom(context->room_url, "OBS Presenter", "obs@obs.com");
    }

    return context;
}

static void rnp_presentation_destroy(void *data) {
    struct rnp_presentation_source *context = (struct rnp_presentation_source *)data;
    if (context) {
        delete context;
    }
}

static uint32_t rnp_presentation_get_width(void *data) {
    struct rnp_presentation_source *context = (struct rnp_presentation_source *)data;
    return context ? context->width : 1920;
}

static uint32_t rnp_presentation_get_height(void *data) {
    struct rnp_presentation_source *context = (struct rnp_presentation_source *)data;
    return context ? context->height : 1080;
}

static void rnp_presentation_get_defaults(obs_data_t *settings) {
    obs_data_set_default_string(settings, "room_url", "https://conferenciaweb.rnp.br/sala/");
    obs_data_set_default_bool(settings, "auto_fit", true);
}

static obs_properties_t *rnp_presentation_get_properties(void *data) {
    (void)data;
    obs_properties_t *props = obs_properties_create();

    obs_properties_add_text(props, "room_url", "URL da Sala RNP:", OBS_TEXT_DEFAULT);
    obs_properties_add_bool(props, "auto_fit", "Ajustar automaticamente à proporção do slide");

    return props;
}

static void rnp_presentation_update(void *data, obs_data_t *settings) {
    struct rnp_presentation_source *context = (struct rnp_presentation_source *)data;
    if (!context) return;

    const char *url = obs_data_get_string(settings, "room_url");
    if (url) context->room_url = url;

    if (!context->room_url.empty()) {
        RNPSession::getInstance().connectToRoom(context->room_url, "OBS Presenter", "obs@obs.com");
    }
}

static void rnp_presentation_video_tick(void *data, float seconds) {
    (void)seconds;
    struct rnp_presentation_source *context = (struct rnp_presentation_source *)data;
    if (!context || !context->source) return;

    struct obs_source_frame frame = {};
    frame.format = VIDEO_FORMAT_BGRA;
    frame.width = context->width;
    frame.height = context->height;
    frame.linesize[0] = context->width * 4;
    frame.data[0] = context->frame_buffer.data();
    frame.timestamp = std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()
    ).count();

    obs_source_output_video(context->source, &frame);
}

struct obs_source_info rnp_presentation_source_info = {
    .id = "rnp_presentation_source",
    .type = OBS_SOURCE_TYPE_INPUT,
    .output_flags = OBS_SOURCE_ASYNC_VIDEO | OBS_SOURCE_DO_NOT_DUPLICATE,
    .get_name = rnp_presentation_get_name,
    .create = rnp_presentation_create,
    .destroy = rnp_presentation_destroy,
    .get_width = rnp_presentation_get_width,
    .get_height = rnp_presentation_get_height,
    .get_defaults = rnp_presentation_get_defaults,
    .get_properties = rnp_presentation_get_properties,
    .update = rnp_presentation_update,
    .video_tick = rnp_presentation_video_tick,
    .icon_type = "presentation"
};
