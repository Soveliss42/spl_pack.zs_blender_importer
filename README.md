# spl_pack.zs_blender_importer
Splatoon 3 stage importer for blender

**WARNING:** add-on made with chatgpt  
These my human notes, below. A summary of what the bot thinks its done is in the zip's readme  
My notes are awful b/c I did not write down everything I discovered, and only kept track of things that still didn't work. Sorry  
I've tried to add notes based on what I remember  

Blender add-on, built for mac, to import splatoon 3 stages  
This is based on the work of Ranidspace's bfres work [https://github.com/ranidspace/BlenderBfresImporter/] and Philippus229's stage importer [https://github.com/Philippus229/io_scene_osi]  

**INSTALL**  
-in the add-on preferences, select your RomFS folder ripped from Splatoon 3. hopefully you don't need to touch the other settings.  
	
**IMPORT**  
-import a pack.zs (found at romfs/pack/scene)  
	-there are individual toggles to import or not import geometry/graffiti/other(non-geometry)/background/etc when selecting a pack.zs file  
	-an import report (whose length/whether it includes all actions can be selected when selecting a file to import) can be found after each import in blender's tmp files  
	-found on mac at (drive)/private/var/folders/lt.......  
	


-Graffiti (or other stuff too) could break sizing/folding/etc if the BFRES LOD Index is set to a value higher than 0, I haven't tested  
-There might be other bugs. Geometry and Graffiti should be pretty good by default, but I haven't conclusively tested much of anything in the non-geometry folder - I know the sun object is at the wrong angle.  
-every time I look away for a week and come back, something new is broken. I'll be real, this is spaghetti code from chatgpt. but it's usually easily fixable given the logs currently set up, and finally appears stable... for me.  

-active team (for color) and sponge inflation levels are accessible via custom properties -> spl_team_color, spl_sponge_inflate  
	-should be a shared custom property on all objects affected by each team color (pillows, sponges, ink rails, rm-no-go signs, etc)  
	-team colors: neutral/alpha/bravo/charlie (charlie only for tricolor)  
	-Sponge inflation may be correct at 1 for cubic sponges but 0.6 for rectangular sponges? (on Yagara) (might vary, idk anymore)  
		-okay sponge inflation is just not set up correctly, some stages the current max size is too small  






**Known issues:**
GRAFFITI:  
-Hiagari04: Graffiti_Tcl_0096, Graffiti_Vlf_0014, Graffiti_Cmn_0057, maybe more - are messed up just a little. I believe they select the anchor panel on the wrong side of fold 0, and then fold 0 folds the opposite direction it should, but I'm not positive. I don't know why just those 2 graffiti.  
	-Graffiti are folded based on foldinfo data in the bcett.byml  
		-foldinfo's (P0X, P0Y) and (P1X, P1Y) form a line/plane (in the graffiti's texture's coordinate space) across which the fold will occur. I don't remember how isrightfold and the centerpoint affect this (sorry) & I might not've fully understood those 2 either.  
			-Not completely implemented correctly - 95%+ look correct (and that took forever), but there are outliers, as listed at the top of this section.  

GEOMETRY
   -sponges team coloring is not quite right - might be the alpha layer?  
-inkrail base (big black sphere) texture when neutral should have teamcolor-neutral applied based on the tcl alpha (and be animated (effects?) but bleh)  

EFFECTS?
-sponge corners don't glow  
-no sz border rings going up  
	-ditto tc checkpoints (also, are the floor squares effects instead of textures?)  
	-sz & tc lines may be created properly as effects  
-ink spitting from inkrail nozzles  


Mode-Specific:
-im not spawning any clams based on map start and/or clam spawn locations... but I could. I have all the parts.  
-tower checkpoint textures not included rn (effects?)  
-tower path (and zone outline) are definitely not correct, but I've left them as-is b/c they're functional visualizers in the correct locations  

LIGHTING:
-lighting balancing from skybox may be incorrect  
	-wtf does cubemapintens do  
-importing the baked lighting properly might correctly fix shadow intensity (instead of leaving world background color on the default setting), but I'm beyond lost  
	-afaik romfs/bake/scene should contain the baked lighting, but no idea how baked lighting works tbh  
-lights might be messed up (eg intensity, radii) but I can't check splatfest stage versions (night versions) to get good references  

MISC:
-interpretation for rotation of the special-reward-photo cameras remains incorrect  
-sound boxes/points/spheres not linked to tracks (prob requires slink work)  
-water depth has no effect on rendering (it should for at least shipshape)  
	-water textures/etc are also still messed up, I think  

LOGIC:
-no logic or animation or whatever nodes for moving objects  
  -eg mahi water level, sturgeon spinners, lemuria, etc  
  	-I think these might import now, but only as visual references  
  	-I think the parenting of some of these linked objects may actually be in the bcett.byml, eg mahi's Lft_FldObj_Hiagari04YaguraFloat srthash 1481123779 has an "spl_ailift_AILiftBancParam" "ToParent" of 16508203447954395776, which is the water's ID for yagura (tc)  
-sponge max size might be too small on some maps. 
  	
TEAMCOLOR:
-mostly fixed. inkrail node 0 items do not properly show their base color specifically in the cycles renderer when set to team neutral  
-I'd like the spl_active_team to be more accessible (not at bottom of custom properties) & not require typing, idk where/how to do that  

NOTES:
-player shadows possibly specified at RomFS/Pack/SingletonPara_v-700_.pack.zs/Gyml/Singleton/game__gfx__CircleShadowMgrParam.game__gfx__CircleShadowMgrParam.bgyml  
	-also "inkcolorcorrection", "modelhighlightparam", "camerafiltercollection", "cameramoduleparam", music frequency stuff, etc  
-I think the minimaps are stored as .bphsh files in the location romfs/pack/actor/[stage].pack.zs/phive/shape/dcc  
	-I've run into what I think was the color controller for this, but idr where  
-Water/ocean colors might be specified in a file in pack/scene/[stage].pack.zs/gyml  
-The sun object's "Latitude" and "Longitude" can be interpreted for MainLight as rotation in degrees of [90-Lat, 0, Long] in xyz. thanks, nintendo.  

OVERALL STRUCTURE (from my memory):
-the pack.zs file are files used to build stages in-game during runtime. Pretty sure.  
	-can be read by switch toolbox [https://github.com/KillzXGaming/Switch-Toolbox]  
	-the bcett.byml file within references all model objects (ones with meshes are bfres files in RomFS/Model) and stores name, location, translation, scale, layer (which game mode this is part of (CMN/TW/SZ/TC/RM/CB/Tricolor), team, and some other stuff  
		-the files are imported by cross-referencing with a list lol (I didn't reverse-engineer how Splatoon 3 assigns IDs, instance IDs, etc)
		-hash & especially SRTHash can be used to find groups/parent-child relationships (iirc)  
		-bcett.byml also contains data for objects that do not have models - sound objects, light objects, rails, areas where super chumps can't land, enemy spawners, etc etc  
			-if it does something in 3d, its probably in the bcett.byml  
			-I have these more or less sorted into folders in blender once you import  
	-the environmental lighting is separate, as files in pack.zs/Gyml/(stage)day or (stage)night or etc  
		-also specifies skybox file  
	-the Graffiti is also separate, in per-mode files in pack.zs/graffiti  
		
