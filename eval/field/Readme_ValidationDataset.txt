Validation Dataset - Horseshoe Beach and Cedar Key, FL
------------------------------------------------------
This dataset includes the validation of virtual damage assessments and first-floor elevation estimations from this project through comparison with field damage assessment data for 28 buildings and Florida Elevation Certificate data for 29 buildings, respectively. 

This data is contained in 2 .csv files:

	-HBCK_FECvsFFEmeas.csv
	-HBCK_Field_VDA_comparison.csv

The following columns are included in HBCK_FECvsFFEmeas.csv:

	-Address				Assessed building address
	-NSI_fd_id				Unique NSI identifier used for geolocation of the building
	-Foundation_Type			Foundation type corresponding to FEMA building diagram number
	-FEMA_No				FEMA building diagram number
	-NSI_FFE_ft				NSI estimated FFE in ft
	-VDA_FFE_ft				Estimated FFE in ft from this study
	-FEC_FFE_ft				FFE in ft reported by Florida Elevation Certificate
	-Diff_VDA_FEC_ft			Absolute difference between VDA_FFE_ft and FEC_FFE_ft
	-Diff_NSI_FEC_ft			Absolute difference between NSI_FFE_ft and FEC_FFE_ft
	-avg_diff_VDA_FEC_ft			Average value of Diff_VDA_FEC_ft for all 28 buildings
	-avg_diff_VDA_FEC_m			Same as previous, in m
	-avg_diff_NSI_FEC_ft			Average value of Diff_NSI_FEC_ft for all 28 buildings
	-avg_diff_NSI_FEC_m			Same as previous, in m

The following columns are included in HBCK_Field_VDA_comparison:

	-Building_No				Building identifier for field/VDA comparison
	-Address				Assessed building address
	-HB_CK					Building location, in either Horseshoe Beach or Cedar Key
	-NSI_fd_id				Unique NSI identifier used for geolocation of the building
	-DS_field				Reported field damage state
	-DS_vda_to_field			VDA damage state mapped to field damage state for comparison, mapping scheme provided below this list
	-Diff_vda_field				Difference between field damage state and mapped VDA to field damage state

VDA to field damage state mapping:

VDA	Field   Damage Description
0,1	0	Negligible to Slight
2,3	1	Moderate
4	2	Substantial to Heavy
5	3	Very Heavy
6	4	Destruction


