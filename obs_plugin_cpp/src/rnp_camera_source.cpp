#include "obs_headers.h"
#include "rnp_sources.h"
#include "rnp_session.h"
#include <string>
#include <vector>
#include <chrono>

struct rnp_camera_source {
    obs_source_t *source;
    std::string room_url;
    std::string user_name;
    std::string email;
    std::string target_user_id; // "all_grid", "active_speaker", ou ID específico
    uint32_t width;
    uint32_t height;
    std::vector<uint8_t> frame_buffer;
    bool active;
};

static const char *rnp_camera_get_name(void *type_data) {
    (void)type_data;
    return "RNP - Câmera de Participante (Webcam)";
}

static void *rnp_camera_create(obs_data_t *settings, obs_source_t *source) {
    struct rnp_camera_source *context = new struct rnp_camera_source();
    context->source = source;
    context->width = 1280;
    context->height = 720;
    context->active = false;
    context->frame_buffer.resize(context->width * context->height * 4, 0);

    // Conectar à sessão RNP se configurada
    const char *url = obs_data_get_string(settings, "room_url");
    const char *name = obs_data_get_string(settings, "user_name");
    const char *target = obs_data_get_string(settings, "target_user");

    if (url && strlen(url) > 0) {
        context->room_url = url;
        context->user_name = name ? name : "OBS Studio";
        context->target_user_id = target ? target : "all_grid";
        RNPSession::getInstance().connectToRoom(context->room_url, context->user_name, "obs@obs.com");
    }

    return context;
}

static void rnp_camera_destroy(void *data) {
    struct rnp_camera_source *context = (struct rnp_camera_source *)data;
    if (context) {
        delete context;
    }
}

static uint32_t rnp_camera_get_width(void *data) {
    struct rnp_camera_source *context = (struct rnp_camera_source *)data;
    return context ? context->width : 1280;
}

static uint32_t rnp_camera_get_height(void *data) {
    struct rnp_camera_source *context = (struct rnp_camera_source *)data;
    return context ? context->height : 720;
}

static void rnp_camera_get_defaults(obs_data_t *settings) {
    obs_data_set_default_string(settings, "room_url", "https://conferenciaweb.rnp.br/sala/");
    obs_data_set_default_string(settings, "user_name", "OBS Studio");
    obs_data_set_default_string(settings, "target_user", "all_grid");
}

static obs_properties_t *rnp_camera_get_properties(void *data) {
    (void)data;
    obs_properties_t *props = obs_properties_create();

    obs_properties_add_text(props, "room_url", "URL da Sala RNP (ConferênciaWeb):", OBS_TEXT_DEFAULT);
    obs_properties_add_text(props, "user_name", "Nome de Exibição no OBS:", OBS_TEXT_DEFAULT);

    obs_property_t *p_target = obs_properties_add_list(
        props, "target_user", "Câmera a Exibir:",
        OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING
    );
    obs_property_list_add_string(p_target, "Todas em Grid Dinâmico", "all_grid");
    obs_property_list_add_string(p_target, "Orador Ativo (Quem está falando)", "active_speaker");

    // Adicionar participantes conhecidos
    auto participants = RNPSession::getInstance().getParticipants();
    for (const auto &user : participants) {
        std::string label = "Câmera: " + user.name;
        obs_property_list_add_string(p_target, label.c_str(), user.userId.c_str());
    }

    return props;
}

static void rnp_camera_update(void *data, obs_data_t *settings) {
    struct rnp_camera_source *context = (struct rnp_camera_source *)data;
    if (!context) return;

    const char *url = obs_data_get_string(settings, "room_url");
    const char *name = obs_data_get_string(settings, "user_name");
    const char *target = obs_data_get_string(settings, "target_user");

    if (url) context->room_url = url;
    if (name) context->user_name = name;
    if (target) context->target_user_id = target;

    if (!context->room_url.empty()) {
        RNPSession::getInstance().connectToRoom(context->room_url, context->user_name, "obs@obs.com");
    }
}

static void rnp_camera_video_tick(void *data, float seconds) {
    (void)seconds;
    struct rnp_camera_source *context = (struct rnp_camera_source *)data;
    if (!context || !context->source) return;

    // Enviar frame transparente ou vídeo para o pipeline do OBS
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

struct obs_source_info rnp_camera_source_info = {
    .id = "rnp_camera_source",
    .type = OBS_SOURCE_TYPE_INPUT,
    .output_flags = OBS_SOURCE_ASYNC_VIDEO | OBS_SOURCE_DO_NOT_DUPLICATE,
    .get_name = rnp_camera_get_name,
    .create = rnp_camera_create,
    .destroy = rnp_camera_destroy,
    .get_width = rnp_camera_get_width,
    .get_height = rnp_camera_get_height,
    .get_defaults = rnp_camera_get_defaults,
    .get_properties = rnp_camera_get_properties,
    .update = rnp_camera_update,
    .video_tick = rnp_camera_video_tick,
    .icon_type = "camera"
};
