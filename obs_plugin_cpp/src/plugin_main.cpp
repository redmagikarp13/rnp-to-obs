#include "obs_headers.h"
#include "rnp_sources.h"
#include "rnp_session.h"
#include <stdio.h>

#define PLUGIN_VERSION "0.1.0"
#define PLUGIN_NAME "RNP Webconferência Bridge"
#define PLUGIN_AUTHOR "RNP OBS Studio Team"

static obs_module_t *obs_module_pointer = nullptr;

extern "C" {

MODULE_EXPORT void obs_module_set_pointer(obs_module_t *module) {
    obs_module_pointer = module;
}

MODULE_EXPORT obs_module_t *obs_current_module(void) {
    return obs_module_pointer;
}

MODULE_EXPORT const char *obs_module_author(void) {
    return PLUGIN_AUTHOR;
}

MODULE_EXPORT const char *obs_module_name(void) {
    return PLUGIN_NAME;
}

MODULE_EXPORT const char *obs_module_description(void) {
    return "Plugin Nativo para captura modular de Câmeras, Apresentação e Chat da Webconferência RNP (BigBlueButton) para o OBS Studio.";
}

MODULE_EXPORT const char *obs_module_ver(void) {
    return PLUGIN_VERSION;
}

MODULE_EXPORT void obs_module_set_locale(const char *locale) {
    (void)locale;
}

MODULE_EXPORT void obs_module_free_locale(void) {
}

MODULE_EXPORT bool obs_module_load(void) {
    blog(LOG_INFO, "[%s v%s] Inicializando plugin nativo RNP Bridge...", PLUGIN_NAME, PLUGIN_VERSION);

    // Registrar fontes nativas no OBS
    register_rnp_sources();

    blog(LOG_INFO, "[%s] Fontes nativas de Câmeras, Slides e Chat registradas com sucesso!", PLUGIN_NAME);
    return true;
}

MODULE_EXPORT void obs_module_unload(void) {
    blog(LOG_INFO, "[%s] Descarregando plugin e finalizando sessões ativas...", PLUGIN_NAME);
    RNPSession::getInstance().disconnect();
}

}
