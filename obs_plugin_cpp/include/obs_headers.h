#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* --------------------------------------------------------------------------
   Definições essenciais da libobs (OBS Studio Plugin API C Interface)
   Permite compilação direta e vinculação dinâmica com obs.dll
   -------------------------------------------------------------------------- */

#define EXPORT __declspec(dllimport)
#define MODULE_EXPORT __declspec(dllexport)

#define OBS_MODULE_SUCCESS 0
#define OBS_MODULE_ERROR -1
#define OBS_MODULE_WARNING 1

enum obs_source_type {
    OBS_SOURCE_TYPE_INPUT,
    OBS_SOURCE_TYPE_FILTER,
    OBS_SOURCE_TYPE_TRANSITION,
    OBS_SOURCE_TYPE_SCENE
};

#define OBS_SOURCE_VIDEO (1 << 0)
#define OBS_SOURCE_AUDIO (1 << 1)
#define OBS_SOURCE_ASYNC (1 << 2)
#define OBS_SOURCE_ASYNC_VIDEO (OBS_SOURCE_ASYNC | OBS_SOURCE_VIDEO)
#define OBS_SOURCE_CUSTOM_DRAW (1 << 3)
#define OBS_SOURCE_INTERACTION (1 << 5)
#define OBS_SOURCE_COMPOSITE (1 << 6)
#define OBS_SOURCE_DO_NOT_DUPLICATE (1 << 7)

enum video_format {
    VIDEO_FORMAT_NONE,
    VIDEO_FORMAT_I420,
    VIDEO_FORMAT_NV12,
    VIDEO_FORMAT_YVYU,
    VIDEO_FORMAT_YUY2,
    VIDEO_FORMAT_UYVY,
    VIDEO_FORMAT_RGBA,
    VIDEO_FORMAT_BGRA,
    VIDEO_FORMAT_BGRX,
    VIDEO_FORMAT_Y800,
    VIDEO_FORMAT_I444,
    VIDEO_FORMAT_BGR3,
    VIDEO_FORMAT_I422,
    VIDEO_FORMAT_I40A,
    VIDEO_FORMAT_I42A,
    VIDEO_FORMAT_YUVA,
    VIDEO_FORMAT_AYUV,
    VIDEO_FORMAT_I010,
    VIDEO_FORMAT_P010,
    VIDEO_FORMAT_I210,
    VIDEO_FORMAT_I412,
    VIDEO_FORMAT_YA2L
};

enum video_colorspace {
    VIDEO_CS_DEFAULT,
    VIDEO_CS_601,
    VIDEO_CS_709,
    VIDEO_CS_SRGB,
    VIDEO_CS_2100_PQ,
    VIDEO_CS_2100_HLG
};

enum video_range_type {
    VIDEO_RANGE_DEFAULT,
    VIDEO_RANGE_PARTIAL,
    VIDEO_RANGE_FULL
};

struct obs_source_frame {
    uint8_t *data[8];
    uint32_t linesize[8];
    uint32_t width;
    uint32_t height;
    uint64_t timestamp;
    enum video_format format;
    float color_matrix[16];
    bool full_range;
    float color_range_min[3];
    float color_range_max[3];
    bool flip;
    uint8_t flags;
    uint8_t trc;
};

/* Tipos Opaque de OBS */
typedef struct obs_source obs_source_t;
typedef struct obs_data obs_data_t;
typedef struct obs_data_array obs_data_array_t;
typedef struct obs_properties obs_properties_t;
typedef struct obs_property obs_property_t;
typedef struct obs_module obs_module_t;

/* Definição de Source Info para registro */
struct obs_source_info {
    const char *id;
    enum obs_source_type type;
    uint32_t output_flags;
    const char *(*get_name)(void *type_data);
    void *(*create)(obs_data_t *settings, obs_source_t *source);
    void (*destroy)(void *data);
    uint32_t (*get_width)(void *data);
    uint32_t (*get_height)(void *data);
    void (*get_defaults)(obs_data_t *settings);
    obs_properties_t *(*get_properties)(void *data);
    void (*update)(void *data, obs_data_t *settings);
    void (*activate)(void *data);
    void (*deactivate)(void *data);
    void (*show)(void *data);
    void (*hide)(void *data);
    void (*video_tick)(void *data, float seconds);
    void (*video_render)(void *data, void *effect);
    void *type_data;
    void (*free_type_data)(void *type_data);
    const char *icon_type;
};

/* Logging */
#define LOG_ERROR 100
#define LOG_WARNING 200
#define LOG_INFO 300
#define LOG_DEBUG 400

enum obs_text_type {
    OBS_TEXT_DEFAULT,
    OBS_TEXT_PASSWORD,
    OBS_TEXT_MULTILINE
};

enum obs_combo_type {
    OBS_COMBO_TYPE_EDITABLE,
    OBS_COMBO_TYPE_LIST
};

enum obs_combo_format {
    OBS_COMBO_FORMAT_INT,
    OBS_COMBO_FORMAT_FLOAT,
    OBS_COMBO_FORMAT_STRING
};

/* Funções C exportadas pela libobs (obs.dll) */
EXPORT uint32_t obs_get_version(void);
EXPORT void blog(int log_level, const char *format, ...);
EXPORT void obs_source_output_video(obs_source_t *source, const struct obs_source_frame *frame);
EXPORT void obs_register_source_s(const struct obs_source_info *info, size_t size);
EXPORT const char *obs_data_get_string(obs_data_t *data, const char *name);
EXPORT void obs_data_set_default_string(obs_data_t *data, const char *name, const char *val);
EXPORT int64_t obs_data_get_int(obs_data_t *data, const char *name);
EXPORT void obs_data_set_default_int(obs_data_t *data, const char *name, int64_t val);
EXPORT bool obs_data_get_bool(obs_data_t *data, const char *name);
EXPORT void obs_data_set_default_bool(obs_data_t *data, const char *name, bool val);

EXPORT obs_properties_t *obs_properties_create(void);
EXPORT obs_property_t *obs_properties_add_text(obs_properties_t *props, const char *name, const char *description, enum obs_text_type type);
EXPORT obs_property_t *obs_properties_add_list(obs_properties_t *props, const char *name, const char *description, enum obs_combo_type type, enum obs_combo_format format);
EXPORT size_t obs_property_list_add_string(obs_property_t *p, const char *name, const char *val);
EXPORT obs_property_t *obs_properties_add_bool(obs_properties_t *props, const char *name, const char *description);
EXPORT obs_property_t *obs_properties_add_button(obs_properties_t *props, const char *name, const char *text, bool (*callback)(obs_properties_t *, obs_property_t *, void *));

#define obs_register_source(info) obs_register_source_s(info, sizeof(struct obs_source_info))

#ifdef __cplusplus
}
#endif
