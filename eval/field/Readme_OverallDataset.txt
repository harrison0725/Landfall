Overall Dataset - Horseshoe Beach and Cedar Key, FL
-------------------------------------------------------
This dataset includes virtual damage assessment (VDA) data and first-floor elevation (FFE) estimations of 1,085 structures impacted by Hurricane Helene (2024) in Horseshoe Beach and Cedar Key, Florida, using primarily pre- and post-storm street-level and aerial imagery data. This dataset also includes publicly available data from the National Structures Inventory (NSI) and building attribute data from Dixie County and Levy County Property Assessors and observations from available imagery. For more information about the dataset, please refer to X. 

This data is contained in 4 csv files:

	-HxHelene_VDA_HB_CK_FinalData.csv: includes all data for buildings in both Horseshoe Beach and Cedar Key, with unique building identification id (FID)
	-HxHelene_VDA_CedarKey.csv: includes all data only for buildings in Cedar Key
	-HxHelene_HorseshoeBeach.csv: includes all data only for buildings in Horseshoe Beach,
	-HxHelene_VDA_HB_CK_FinalData_reduced.csv: stripped down version of the final dataset including only the FID, NSI fd_id, Location, x, y, eff_yr_built, structure val,
	 building_type, foundation_type, mitigation_strategy, FEMA_building_diagram_number, grd_el_ft, FFE_NAVD88_ft, FFE_NAVD88_ft_from_grade, and DS_overall

The following columns are contained in each of the first 3 csv files associated with this dataset:

	-FID			            Unique VDA identifier for each building
	-fd_id	                            NSI identifier for each building
	-cbfips                             2010 Census Block that contains the structure (NSI)
	-st_damcat                          NSI damage category of the structure
	-occtype                            NSI damage function of occupancy type of the structure
	-bldgtype                           NSI building type of the structure
	-num_story                          NSI number of stories of the structure
	-sqft                               NSI square footage of the structure
	-found_type                         NSI foundation type of the structure
	-found_ht                           NSI foundation height of the structure not including ground elevation
	-med_yr_blt                         Median year built of the structures within the Census Block (NSI)
	-val_struct                         NSI structure value
	-val_cont                           NSI value of contents within the structure
	-val_vehic                          NSI median value of vehicles stored at the property within the Census Block
	-ftprintid                          Identifier of the building footprint record used for estimating fields such as sqft and num_story (NSI)
	-ftprintsrc                         The source of the utilized footprint (NSI)
	-x                                  x-coordinate of the structure
	-y                                  y-coordinate of the structure
	-firmzone                           FEMA flood-zone in which the structure resides
	-usgs_grd_el                        Ground elevation, in meters, of the structure from USGS DEM
	-grd_el_ft                          Ground elevation, in feet, of the structure from USGS DEM
	-num_stories                        Number of stories of the structure as identified by assessors
	-structure_val                      Value of the structure determined by assessors from property appraiser data
	-eff_yr_built                       Effective year built of the structure determined by assessors from property appraiser data
        -occ_type                           Assessed occupancy type with values corresponding to the NSI st_damcat
        -occ_subtype                        Assessed occupancy type with values corresponding to NSI damage function of occupancy type of the structure
        -building visible in                Indicates whether the building was visible in StEER or OSU street-level imagery for assessment
         streetview?(yes/no)
        -roofline_measurement_ft            Roofline measurement in ft from municipal GIS map or Google Earth Pro
        -roofline_measurement_px            Roofline measurement in pixels in street-level imagery
        -floor_measurement_px               Measurement from the ground to the bottom of the first floor, in pixels, in street-level imagery
        -FFE_NAVD88                         FFE estimate in NAVD88, by converting the floor measurement in pixels to ft from grade and adding grd_el_ft
        -FFE_elevation_description          Objective classification of the elevation status of the building as elevated or not elevated
        -FFE_note                           Assessor note about the FFE estimation process 
        -building_type                      Assessed building type. M=Masonry, W=wood, S=steel, C=concrete, H=manufactured
        -foundation_type                    Assessed foundation type. P=piers, I=piles, F=fill, S=slab/raised slab, W=wall, C=crawlspace
        -mitigation_strategy                Categorical classification of mitigation strategy employed by the structure
        -DS_roof                            Roof damage state (DS0-DS5)
        -DS_walls/floors                    Damage state for walls and elevated floors (DS0-DS5) 
        -DS_foundation                      Foundation damage state (DS0-DS6)
        -DS_attached/detached_structures    Damage state for attached and detached structures (DS0-DS3)
        -DS_openings                        Damage state for windows and doors (DS0-DS3)
        -DS_overall                         Overall rated damage state, corresponding to the highest component damage state
        -notes                              Timestamp and notes to provide context for each assessment

The reduced dataset includes the addition of the following:
    
        -Location                           Identifies if the building is in Horseshoe Beach or Cedar Key
        -FFE_NAVD88_ft_from_grade           Estimated FFE from grade, not including ground elevation
	
	