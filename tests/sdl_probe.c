/* Original native ABI/input probe; not part of the distributed frontend. */
#define SDL_MAIN_HANDLED
#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static void require(bool ok, const char *phase) {
    if (!ok) { fprintf(stderr, "%s: %s\n", phase, SDL_GetError()); exit(1); }
}
int main(void) {
    SDL_SetMainReady();
    int version = SDL_GetVersion();
    require(SDL_VERSIONNUM_MAJOR(version) == 3 && SDL_VERSIONNUM_MINOR(version) == 4, "SDL 3.4 ABI");
    require(SDL_SetHint(SDL_HINT_AUDIO_DRIVER, "dummy"), "Select private dummy audio");
    require(SDL_Init(SDL_INIT_GAMEPAD | SDL_INIT_AUDIO), "Initialize gamepad and audio input");
    require(strcmp(SDL_GetCurrentAudioDriver(), "dummy") == 0, "Use dummy audio backend");
    SDL_AudioSpec audio = { SDL_AUDIO_S16, 2, 48000 };
    SDL_AudioStream *stream = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &audio, NULL, NULL);
    require(stream != NULL, "Open SDL3 audio stream");
    short silence[128] = {0};
    require(SDL_PutAudioStreamData(stream, silence, sizeof(silence)), "Queue audio frames");
    require(SDL_GetAudioStreamQueued(stream) > 0, "Read queued audio frames");
    require(SDL_ResumeAudioStreamDevice(stream), "Start dummy audio stream");
    SDL_DestroyAudioStream(stream);
    SDL_VirtualJoystickDesc desc;
    SDL_INIT_INTERFACE(&desc);
    desc.type = SDL_JOYSTICK_TYPE_GAMEPAD;
    desc.naxes = SDL_GAMEPAD_AXIS_COUNT;
    desc.nbuttons = SDL_GAMEPAD_BUTTON_COUNT;
    desc.vendor_id = 0x1209;
    desc.product_id = 0x0001;
    desc.name = "Broomm CI gamepad";
    SDL_JoystickID id = SDL_AttachVirtualJoystick(&desc);
    require(id != 0 && SDL_IsGamepad(id), "Discover native virtual gamepad");
    SDL_Joystick *joystick = SDL_OpenJoystick(id);
    SDL_Gamepad *gamepad = SDL_OpenGamepad(id);
    require(joystick != NULL && gamepad != NULL, "Open same-runtime identity");
    require(SDL_SetJoystickVirtualButton(joystick, SDL_GAMEPAD_BUTTON_SOUTH, true), "Set button");
    SDL_UpdateGamepads();
    require(SDL_GetGamepadButton(gamepad, SDL_GAMEPAD_BUTTON_SOUTH), "Read selected input");
    require(SDL_SetJoystickVirtualButton(joystick, SDL_GAMEPAD_BUTTON_SOUTH, false), "Release button");
    SDL_UpdateGamepads();
    require(!SDL_GetGamepadButton(gamepad, SDL_GAMEPAD_BUTTON_SOUTH), "Read neutral release");
    SDL_CloseGamepad(gamepad);
    SDL_CloseJoystick(joystick);
    require(SDL_DetachVirtualJoystick(id), "Detach native test device");
    SDL_Quit();
    puts("PASS SDL 3.4 ABI, dummy audio stream, native identity, virtual gamepad input and release");
    return 0;
}

