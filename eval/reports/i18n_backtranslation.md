# Arabic back-translation check (M2-01)

A machine drift check, not a validation. No native speaker reviewed these strings, and no accuracy figure is claimed. Each Arabic string was translated back to English by the model without seeing the English. The model then judged whether the back-translation means the same as the English original, and chrF (sacrebleu 2.6.0) compared the two. A string is flagged when the model says the meaning differs or chrF < 40.

- Report written 2026-09-27T17:58:52+00:00; task `translation_check`, model `qwen3.5-9b-4bit` (served: `mlx-community/Qwen3.5-9B-MLX-4bit@938d8919941c6e7efd3c7150eff7fe9d12afa631`).
- Strings checked: 461 (99 `safety.`).
- Flagged: 113 (0 `safety.`; 0 of them listed in docs/i18n/unresolved.md).
- Unresolved after five rewrites, shown with their English original beside the Arabic: 5 (docs/i18n/unresolved.md).

Flagged strings, `safety.` keys first:

| App | Key | English | Back-translation | chrF | Same meaning | Issue |
| --- | --- | --- | --- | --- | --- | --- |
| companion | `claim.code` | Hand-over code | Transition code | 20.2 | yes |  |
| companion | `claim.status` | See my place in the queue | View my turn in the waiting list | 18.2 | yes |  |
| companion | `claim.submit` | Continue | Follow | 2.2 | yes |  |
| companion | `claim.welcome` | Connected, <ltr>{initials}</ltr> | Contacted, <ltr>{initials}</ltr> | 85.7 | no | Verb meaning changed |
| companion | `header.app` | CareFlow | Circulo | 11.0 | no | CareFlow vs Circulo |
| companion | `intake.answers.breathing` | Breathing problems | Difficulty breathing | 38.0 | yes |  |
| companion | `intake.answers.ok` | OK | Okay | 20.8 | yes |  |
| companion | `intake.failed` | Your answer did not go through. Please try again. | You did not submit your answer. Please try again. | 63.5 | no | Cause of error differs |
| companion | `intake.greeting` | Welcome, <ltr>{initials}</ltr>. | Hello, <ltr>{initials}</ltr>. | 77.8 | no | Greeting changed |
| companion | `intake.scale` | Tap a number from 0 to 10 | Press a number from 0 to 10 | 79.7 | no | Action differs: Tap vs Press |
| companion | `intake.send` | Send | Submit | 5.7 | no | Action differs: Send vs Submit |
| companion | `intake.status` | See my place in the queue | My place in the queue | 79.7 | no | Missing 'See' |
| companion | `meta.title` | CareFlow companion | Circulo - Facilities | 9.7 | no | Companion vs Facilities |
| companion | `status.failed` | Your status could not be loaded. | Failed to load your status. | 38.1 | yes |  |
| companion | `status.paused` | Live updates paused; checking every 15 seconds. | Live updates disabled; checking every 15 seconds. | 82.8 | no | Action status differs |
| companion | `status.stale` | We could not refresh your status. This is the last one we had. | Failed to update your status. This is our last status. | 40.4 | no | Subject and action differ |
| companion | `status.title` | Your place in the queue | Your turn in the waiting list | 24.8 | yes |  |
| companion | `status.updated` | Updated {time}. | Last updated {time}. | 85.1 | no | Time reference differs |
| kiosk | `checkin.backspace` | Delete the last digit | Delete last number | 37.6 | yes |  |
| kiosk | `checkin.display` | Keypad ID | Keyboard code | 16.2 | yes |  |
| kiosk | `checkin.failed` | We couldn't find a match. Please try again or see reception. | No match found. Try again or go to reception. | 39.5 | yes |  |
| kiosk | `checkin.needsSetup` | This kiosk is not signed in yet. | This device has not been logged in yet. | 37.1 | yes |  |
| kiosk | `checkin.title` | Check in | Login | 9.6 | yes |  |
| kiosk | `header.app` | CareFlow check-in | Arrival registration at Kerfloo | 9.2 | no | Brand name mismatch |
| kiosk | `home.arabic` | العربية | Arabic | 0.0 | no | Script direction mismatch |
| kiosk | `idle.countdown` | Starting over in {seconds} seconds. | We will start again in {seconds} seconds. | 64.2 | no | Subject mismatch |
| kiosk | `idle.stay` | I'm still here | Still here | 60.4 | no | Missing subject |
| kiosk | `idle.title` | Are you still there? | Are you still here? | 75.1 | no | Location mismatch |
| kiosk | `intake.answers.breathing` | Breathing problems | Difficulty in breathing | 37.0 | yes |  |
| kiosk | `intake.answers.ok` | OK | Okay | 20.8 | yes |  |
| kiosk | `intake.failed` | That did not go through. Please try again, or ask a nurse. | Your answer did not reach. Try again or ask the nurse for help. | 36.2 | no | Subject and help recipient differ |
| kiosk | `intake.phone.expires` | This code works for {seconds} more seconds. | This code works for another {seconds} seconds. | 75.9 | no | works for vs works for another |
| kiosk | `intake.phone.stay` | Stay on the kiosk | Stay on the device | 55.7 | no | kiosk vs device |
| kiosk | `meta.description` | Self check-in kiosk. Research prototype. | Self-recording device. Research prototype. | 57.5 | no | Self check-in vs Self-recording |
| kiosk | `meta.title` | CareFlow kiosk | Kerfloo - Registration Machine | 10.7 | no | Brand name mismatch |
| kiosk | `setup.failed` | The kiosk could not be signed in. Check the ID and secret. | Device login failed. Check the number and secret code. | 37.3 | yes |  |
| kiosk | `setup.submit` | Sign in kiosk | Device login | 9.2 | yes |  |
| kiosk | `setup.title` | Kiosk setup | Device settings | 13.0 | no | Context mismatch |
| kiosk | `setup.working` | Signing in… | Logging in... | 29.8 | yes |  |
| kiosk | `vitals.failed` | The vitals device could not start. Please ask a nurse. | Failed to start the measurement device. Please notify the nurse. | 38.4 | yes |  |
| kiosk | `vitals.simulated` | Simulated device | Simulation device | 60.4 | no | Simulated vs Simulation |
| staff | `admin.model` | Model or tool | AI form or tool | 39.3 | yes |  |
| staff | `admin.title` | Model providers | AI form providers | 55.7 | no | Model vs AI form |
| staff | `adminStatus.model` | Model or tool | AI model or tool | 83.7 | no | Specificity of tool type |
| staff | `adminStatus.ports` | Intake parts | Reception parts | 30.6 | no | Intake vs Reception |
| staff | `adminStatus.providers` | Model providers | AI model providers | 88.0 | no | Specificity of provider type |
| staff | `adminStatus.refresh` | Refresh | Update | 2.5 | no | Action differs |
| staff | `adminStatus.ruleset` | Safety ruleset | Safety rules | 83.7 | no | Scope differs |
| staff | `adminStatus.state.down` | Down | Stopped | 5.4 | no | Down vs Stopped |
| staff | `adminStatus.state.ok` | OK | Salem | 0.0 | no | OK vs Salem |
| staff | `adminStatus.stub` | Stub | Temporary alternative | 3.5 | no | Stub vs Temporary alternative |
| staff | `adminStatus.urgencyModel` | Urgency model | AI model for priority estimation | 22.6 | no | Specificity of model type |
| staff | `board.title` | Queue board: {department} | Waiting Room: {department} | 51.8 | no | Queue board vs Waiting Room |
| staff | `briefing.cites` | {count, plural, one {Show the fact} other {Show # facts}} | {count, plural, one {Show fact} other {Show # facts}} | 88.9 | no | Original says 'Show the fact', back-translation says 'Show fact' |
| staff | `briefing.setByNurse` | set by the nurse | Set by nurse | 37.7 | yes |  |
| staff | `common.failed` | The data could not be loaded. | Failed to load data. | 20.7 | no | Active voice vs passive voice |
| staff | `common.signIn` | Sign in | Log in | 14.5 | yes |  |
| staff | `doctor.arrived` | Arrived | Arrival Time | 42.7 | no | Arrived vs Arrival Time |
| staff | `doctor.open` | Open briefing | Open Summary | 17.0 | no | Briefing vs Summary |
| staff | `doctor.title` | Doctor briefing | Doctor Summary | 30.5 | no | Briefing vs Summary |
| staff | `header.app` | CareFlow staff | Kerfloo for staff | 27.3 | no | Brand name mismatch |
| staff | `home.board` | Queue board | Waiting Room | 3.3 | no | Queue board vs Waiting Room |
| staff | `home.login` | Sign in | Login | 14.5 | yes |  |
| staff | `login.intro` | Development sign-in for synthetic staff accounts. | Test Login for Artificial Staff Accounts. | 29.3 | no | Development vs Test |
| staff | `login.submit` | Sign in | Login | 14.5 | no | Sign in vs Login |
| staff | `login.title` | Sign in | Login | 14.5 | yes |  |
| staff | `login.working` | Signing in… | Logging in... | 29.8 | yes |  |
| staff | `meta.description` | Nurse station, doctor briefing, queue board and admin. Research prototype. | Nursing station and doctor summary and waiting board and administration. Research prototype. | 59.0 | no | Nurse station vs Nursing station |
| staff | `meta.title` | CareFlow staff | Kerfloo - Staff | 20.5 | no | Brand name mismatch |
| staff | `news2.acvpu` | Consciousness (ACVPU) | Level of consciousness (ACVPU) | 87.2 | no | Scope of assessment differs |
| staff | `news2.acvpuOptions.U` | Unresponsive | Does not respond | 37.4 | yes |  |
| staff | `news2.acvpuOptions.V` | Responds to voice | Responds to sound | 60.3 | no | voice vs sound |
| staff | `news2.band.low-medium` | Low-medium risk (a single 3) | Low to moderate risk (single value 3) | 35.9 | yes |  |
| staff | `news2.band.medium` | Medium risk | Moderate risk | 22.8 | yes |  |
| staff | `news2.oxygen` | On supplemental oxygen | On additional oxygen | 35.8 | yes |  |
| staff | `news2.params.acvpu` | ACVPU | Level of consciousness | 0.0 | yes |  |
| staff | `news2.params.heart_rate` | Heart rate | Pulse | 2.4 | yes |  |
| staff | `news2.params.spo2` | SpO2 | Oxygen saturation | 3.9 | yes |  |
| staff | `news2.save` | Save vitals | Save vital signs | 90.1 | no | vitals vs vital signs |
| staff | `nurse.age` | {age} y | {age} years | 79.5 | no | Unit abbreviation vs full word |
| staff | `nurse.ageSex` | Age, sex | Age, Gender | 28.1 | no | Age, sex vs Age, Gender |
| staff | `nurse.arrived` | Arrived | Arrival Time | 42.7 | no | Arrived vs Arrival Time |
| staff | `nurse.live` | Live | Direct | 11.4 | no | Status terminology difference |
| staff | `nurse.noLevel` | Not assessed | Not Yet Rated | 17.5 | yes |  |
| staff | `nurse.paused` | Live updates paused. Checking every 5 seconds. | Live updates stopped. Check every 5 seconds. | 69.1 | no | Verb tense and phrasing difference |
| staff | `nurse.queue.close` | Seen (free the room) | Screening completed (room cleared) | 23.6 | yes |  |
| staff | `nurse.queue.done.close` | {code} seen; the room is free. | {code} screened; room cleared. | 35.4 | yes |  |
| staff | `nurse.queue.done.no-show` | {code} marked as a no-show. | {code} registered as absent. | 30.4 | yes |  |
| staff | `nurse.queue.noShow` | No-show | Did not attend | 2.1 | yes |  |
| staff | `nurse.queue.status.called` | Called | Summoned | 8.3 | yes |  |
| staff | `nurse.queue.title` | Queue | Waiting list | 0.0 | yes |  |
| staff | `nurse.sessionTitle` | Triage | Sorting | 8.1 | no | Triage vs Sorting |
| staff | `nurse.state` | State | Status | 51.3 | no | State vs Status |
| staff | `nurse.states.arrived` | Arrived | Present | 4.8 | no | Arrived vs Present |
| staff | `nurse.states.intake` | In intake | In the intake survey | 50.5 | no | Missing 'survey' |
| staff | `nurse.states.no_show` | No-show | Absent | 2.5 | yes |  |
| staff | `nurse.states.queued` | Queued | In queue | 33.5 | yes |  |
| staff | `nurse.suggested` | Suggested level | Proposed Level | 18.0 | yes |  |
| staff | `nurse.title` | Nurse worklist | Nursing Work List | 29.3 | yes |  |
| staff | `nurse.unknown` | Not recorded | Not registered | 31.3 | no | Medical term vs administrative term |
| staff | `provenance.EHR` | EHR | Medical record | 0.0 | no | EHR vs Medical record |
| staff | `provenance.MODEL_INFERRED` | Model inferred | Model inference | 71.9 | no | Model inferred vs Model inference |
| staff | `source.ehr` | EHR record: {type} {id} (version {version}). | Patient record: {type} {id} (version {version}). | 89.8 | no | EHR vs Patient record |
| staff | `source.missing` | This line's source could not be verified. | Failed to verify the source of this line. | 36.6 | yes |  |
| staff | `source.openEhr` | Open in the EHR | Open in health record | 37.5 | yes |  |
| staff | `source.show` | Show where this came from | View the source of this information | 17.9 | yes |  |
| staff | `triage.none` | No model suggestion. Choose a level. | No proposal from the model. Select a level. | 30.5 | yes |  |
| staff | `triage.reasonCodes.patient_deteriorated` | Patient deteriorated | Patient condition deterioration | 64.5 | no | Differing phrasing for patient decline |
| staff | `triage.submitOverride` | Set level {level} | Assign level {level} | 72.0 | no | Different action verb |
| staff | `triage.submitOverrideNoLevel` | Choose a level | Select level | 25.7 | yes |  |
| staff | `triage.suggested` | Suggested {label} | Proposal {label} | 33.8 | yes |  |
| staff | `triage.title` | Triage | Sorting | 8.1 | no | Triage vs Sorting |
| staff | `triage.why` | Why | Reason | 0.0 | no | Why vs Reason |
