# Repo Notes for Codex Agents

- The recovered Quest APK is `com.Xigbee.FrankaBot` / `FrankaBotControllerTracking`.
- If pose frames become exact `0,0,0` while the Quest is visibly awake, suspect Meta/Oculus system UI focus stealing rather than a network bug. Run `scripts/start_frankabot.sh --no-install` again, or manually force-stop `com.oculus.panelapp.library` and `com.oculus.store`, then restart `com.Xigbee.FrankaBot/com.unity3d.player.UnityPlayerActivity`.
- The live web calibration defaults to `calibrations/quest_teleop_frame.json`; named profiles are saved as `calibrations/<profile>.json`; the maintained ManiSkill/MuJoCo entry point is `scripts/run_quest_session.sh`.
