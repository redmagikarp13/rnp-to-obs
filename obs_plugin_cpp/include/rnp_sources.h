#pragma once

#include "obs_headers.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Registros das fontes no OBS Studio */
extern struct obs_source_info rnp_camera_source_info;
extern struct obs_source_info rnp_presentation_source_info;
extern struct obs_source_info rnp_chat_source_info;

void register_rnp_sources(void);

#ifdef __cplusplus
}
#endif
