/* Original no-ROM libretro core: verifies frontend command lifecycle only. */
#include <libretro.h>
#include <string.h>
static retro_environment_t environment;
static retro_video_refresh_t video;
static retro_audio_sample_batch_t audio;
static retro_input_poll_t poll_input;
static retro_input_state_t input;
static unsigned short pixels[16 * 16];
RETRO_API void retro_set_environment(retro_environment_t f) {
    environment = f;
    bool no_game = true;
    environment(RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME, &no_game);
}
RETRO_API void retro_set_video_refresh(retro_video_refresh_t f) { video = f; }
RETRO_API void retro_set_audio_sample(retro_audio_sample_t f) { (void)f; }
RETRO_API void retro_set_audio_sample_batch(retro_audio_sample_batch_t f) { audio = f; }
RETRO_API void retro_set_input_poll(retro_input_poll_t f) { poll_input = f; }
RETRO_API void retro_set_input_state(retro_input_state_t f) { input = f; }
RETRO_API void retro_init(void) { }
RETRO_API void retro_deinit(void) { }
RETRO_API unsigned retro_api_version(void) { return RETRO_API_VERSION; }
RETRO_API void retro_get_system_info(struct retro_system_info *info) {
    memset(info, 0, sizeof(*info));
    info->library_name = "Broomm CI";
    info->library_version = "1";
    info->valid_extensions = "bin";
    info->need_fullpath = false;
}
RETRO_API void retro_get_system_av_info(struct retro_system_av_info *info) {
    memset(info, 0, sizeof(*info));
    info->geometry.base_width = info->geometry.max_width = 16;
    info->geometry.base_height = info->geometry.max_height = 16;
    info->geometry.aspect_ratio = 1.0f;
    info->timing.fps = 60;
    info->timing.sample_rate = 48000;
}
RETRO_API void retro_set_controller_port_device(unsigned port, unsigned device) { (void)port; (void)device; }
RETRO_API void retro_reset(void) { }
RETRO_API void retro_run(void) {
    static int16_t silence[800 * 2];
    poll_input();
    (void)input(0, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_A);
    video(pixels, 16, 16, 16 * sizeof(*pixels));
    audio(silence, 800);
}
RETRO_API size_t retro_serialize_size(void) { return 0; }
RETRO_API bool retro_serialize(void *data, size_t size) { (void)data; (void)size; return false; }
RETRO_API bool retro_unserialize(const void *data, size_t size) { (void)data; (void)size; return false; }
RETRO_API void retro_cheat_reset(void) { }
RETRO_API void retro_cheat_set(unsigned index, bool enabled, const char *code) { (void)index; (void)enabled; (void)code; }
RETRO_API bool retro_load_game(const struct retro_game_info *info) { (void)info; return true; }
RETRO_API bool retro_load_game_special(unsigned type, const struct retro_game_info *info, size_t count) { (void)type; (void)info; (void)count; return false; }
RETRO_API void retro_unload_game(void) { }
RETRO_API unsigned retro_get_region(void) { return RETRO_REGION_NTSC; }
RETRO_API void *retro_get_memory_data(unsigned id) { (void)id; return NULL; }
RETRO_API size_t retro_get_memory_size(unsigned id) { (void)id; return 0; }

