"""Baut die Marskolonie in Blender aus world.json und rendert die Einstellungen eines Reels.

Aufruf (headless):
  blender -b -P blender/colony_scene.py -- --world state/mars/world.json \
      --plan work/mars/<datum>/shots.json --out work/mars/<datum>/frames

Die Szene wird bei jedem Lauf vollständig aus dem gespeicherten Weltzustand
erzeugt. Gleicher Zustand = identische Kolonie (alle Zufallswerte sind geseedet).
"""

import argparse
import json
import math
import os
import random
import sys
import time

import bmesh
import bpy
from mathutils import Vector, noise

CELL = 9.0
ROAD_W = 2.6


# --------------------------------------------------------------------------- Setup

def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--world", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--samples", type=int, default=16)
    p.add_argument("--engine", choices=("CYCLES", "EEVEE"), default="EEVEE")
    p.add_argument("--scale", type=int, default=100, help="Auflösung in Prozent")
    p.add_argument("--stills", action="store_true", help="Nur ein Vorschaubild pro Einstellung")
    p.add_argument("--save-blend", default=None)
    return p.parse_args(argv)


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    return scene


def setup_render(scene, plan, a):
    r = scene.render
    r.use_persistent_data = True
    if a.engine == "EEVEE":
        r.engine = "BLENDER_EEVEE"
        r.resolution_x = plan.get("width", 1080)
        r.resolution_y = plan.get("height", 1920)
        r.resolution_percentage = a.scale
        r.fps = plan.get("fps", 24)
        r.image_settings.file_format = "JPEG"
        r.image_settings.quality = 92
        e = scene.eevee
        e.taa_render_samples = a.samples
        for attr, val in (("use_shadows", True), ("use_raytracing", True), ("shadow_ray_count", 1),
                          ("use_gtao", True), ("fast_gi_method", "GLOBAL_ILLUMINATION")):
            try:
                setattr(e, attr, val)
            except (AttributeError, TypeError):
                pass
        try:
            scene.view_settings.view_transform = "AgX"
            scene.view_settings.look = "AgX - Medium High Contrast"
        except TypeError:
            pass
        print("Render-Gerät: EEVEE")
        return "EEVEE"
    r.engine = "CYCLES"
    r.resolution_x = plan.get("width", 1080)
    r.resolution_y = plan.get("height", 1920)
    r.resolution_percentage = a.scale
    r.fps = plan.get("fps", 24)
    r.image_settings.file_format = "JPEG"
    r.image_settings.quality = 92
    r.use_motion_blur = False
    c = scene.cycles
    c.samples = a.samples
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.05
    c.use_denoising = True
    c.max_bounces = 4
    c.diffuse_bounces = 2
    c.glossy_bounces = 2
    c.transmission_bounces = 4
    c.transparent_max_bounces = 4
    c.caustics_reflective = False
    c.caustics_refractive = False
    try:
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass
    device = "CPU"
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for backend in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
            try:
                prefs.compute_device_type = backend
            except TypeError:
                continue
            prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type != "CPU"]
            if gpus:
                for d in prefs.devices:
                    d.use = d.type != "CPU"
                device = "GPU"
                break
    except Exception as e:  # noqa: BLE001
        print("GPU-Erkennung fehlgeschlagen:", e)
    c.device = device
    if device == "CPU":
        c.denoiser = "OPENIMAGEDENOISE"
    print(f"Render-Gerät: {device}")
    return device


# --------------------------------------------------------------------------- Materialien

MATS = {}


def mat(name, color, metallic=0.0, rough=0.5, emission=None, strength=0.0, transmission=0.0, alpha=1.0):
    if name in MATS:
        return MATS[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = rough
    if emission:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1)
        bsdf.inputs["Emission Strength"].default_value = strength
    if transmission:
        bsdf.inputs["Transmission Weight"].default_value = transmission
        bsdf.inputs["IOR"].default_value = 1.2
    if alpha < 1:
        bsdf.inputs["Alpha"].default_value = alpha
    if transmission or alpha < 1:
        # EEVEE braucht Blended-Rendering für Transparenz; Cycles ignoriert das.
        for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND"),
                          ("use_raytrace_refraction", True), ("use_backface_culling", False)):
            try:
                setattr(m, attr, val)
            except (AttributeError, TypeError):
                pass
    MATS[name] = m
    return m


def terrain_material():
    m = bpy.data.materials.new("mars_ground")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    bsdf.inputs["Roughness"].default_value = 0.95
    tex = nt.nodes.new("ShaderNodeTexNoise")
    tex.inputs["Scale"].default_value = 0.08
    tex.inputs["Detail"].default_value = 8
    coord = nt.nodes.new("ShaderNodeTexCoord")
    nt.links.new(coord.outputs["Object"], tex.inputs["Vector"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.32, 0.09, 0.03, 1)
    ramp.color_ramp.elements[1].color = (0.72, 0.30, 0.12, 1)
    nt.links.new(tex.outputs["Fac"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    fine = nt.nodes.new("ShaderNodeTexNoise")
    fine.inputs["Scale"].default_value = 3.0
    fine.inputs["Detail"].default_value = 6
    nt.links.new(coord.outputs["Object"], fine.inputs["Vector"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.35
    nt.links.new(fine.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return m


def init_materials():
    mat("hull", (0.62, 0.62, 0.60), metallic=0.25, rough=0.38)
    mat("hull_dark", (0.25, 0.26, 0.28), metallic=0.6, rough=0.4)
    mat("accent", (0.95, 0.42, 0.08), rough=0.4)
    mat("glass", (0.6, 0.8, 0.95), rough=0.05, transmission=1.0, alpha=0.28)
    mat("window", (0.05, 0.1, 0.15), emission=(1.0, 0.75, 0.45), strength=6)
    mat("light_cyan", (0.1, 0.4, 0.5), emission=(0.2, 0.9, 1.0), strength=12)
    mat("light_red", (0.5, 0.05, 0.05), emission=(1.0, 0.1, 0.05), strength=15)
    mat("light_green", (0.05, 0.4, 0.1), emission=(0.3, 1.0, 0.4), strength=8)
    mat("solar", (0.02, 0.05, 0.18), metallic=0.8, rough=0.15)
    mat("metal", (0.55, 0.56, 0.58), metallic=0.9, rough=0.3)
    mat("road", (0.12, 0.09, 0.08), rough=0.85)
    mat("pad", (0.42, 0.30, 0.24), rough=0.8)
    mat("plants", (0.05, 0.35, 0.08), emission=(0.2, 0.8, 0.2), strength=1.5)
    mat("suit", (0.92, 0.92, 0.9), rough=0.6)
    mat("visor", (0.9, 0.55, 0.1), metallic=1.0, rough=0.1)
    mat("rock", (0.30, 0.10, 0.05), rough=1.0)
    mat("frame", (0.8, 0.82, 0.85), metallic=0.7, rough=0.3)
    mat("ring_glow", (0.2, 0.6, 1.0), emission=(0.3, 0.8, 1.0), strength=25)


# --------------------------------------------------------------------------- Geometrie

def empty(name, loc=(0, 0, 0), parent=None):
    e = bpy.data.objects.new(name, None)
    bpy.context.collection.objects.link(e)
    e.location = loc
    e.parent = parent
    return e


def part(kind, parent, loc=(0, 0, 0), scale=(1, 1, 1), m="hull", rot=(0, 0, 0), smooth=False, **kw):
    if kind == "cyl":
        bpy.ops.mesh.primitive_cylinder_add(vertices=kw.get("v", 32), radius=1, depth=1)
    elif kind == "sphere":
        bpy.ops.mesh.primitive_uv_sphere_add(segments=kw.get("v", 32), ring_count=kw.get("rings", 16), radius=1)
        smooth = True
    elif kind == "cube":
        bpy.ops.mesh.primitive_cube_add(size=1)
    elif kind == "cone":
        bpy.ops.mesh.primitive_cone_add(vertices=kw.get("v", 32), radius1=1, radius2=kw.get("r2", 0), depth=1)
    elif kind == "torus":
        # Echter Ring: Hauptradius aus scale[0], Rohrdicke bleibt rund.
        bpy.ops.mesh.primitive_torus_add(major_radius=scale[0], minor_radius=kw.get("minor", 0.1),
                                         major_segments=kw.get("v", 48), minor_segments=8)
        scale = (1, 1, 1)
        smooth = True
    elif kind == "ico":
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=kw.get("sub", 3), radius=1)
    else:
        raise ValueError(kind)
    o = bpy.context.active_object
    o.parent = parent
    o.location = loc
    o.scale = scale
    o.rotation_euler = rot
    o.data.materials.append(MATS[m])
    if smooth:
        for poly in o.data.polygons:
            poly.use_smooth = True
    return o


def dome(parent, r, z=0.0, base_h=1.0, m="hull", windows=True):
    part("cyl", parent, (0, 0, base_h / 2), (r, r, base_h), m="hull_dark")
    part("sphere", parent, (0, 0, base_h), (r, r, r * 0.85), m=m)
    if windows:
        part("torus", parent, (0, 0, base_h + r * 0.3), (r * 0.93, r * 0.93, 1), m="window", minor=0.12)
    part("cube", parent, (r + 0.6, 0, 0.9), (1.6, 1.4, 1.8), m="hull")
    part("cube", parent, (r + 1.45, 0, 0.9), (0.1, 0.9, 1.4), m="window")


def geodesic(parent, r, thickness=0.25, glass=True):
    if glass:
        part("sphere", parent, (0, 0, 0), (r, r, r * 0.9), m="glass", v=64, rings=32)
    f = part("ico", parent, (0, 0, 0), (r * 1.005, r * 1.005, r * 0.905), m="frame", sub=3)
    mod = f.modifiers.new("wire", "WIREFRAME")
    mod.thickness = thickness / r
    mod.use_even_offset = False
    return f


def solar_panel(parent, x, y, w=2.0, d=1.2):
    part("cyl", parent, (x, y, 0.5), (0.06, 0.06, 1.0), m="metal", v=8)
    part("cube", parent, (x, y, 1.05), (w, d, 0.06), m="solar", rot=(math.radians(25), 0, 0))


def tank(parent, x, y, r=1.0, h=4.0, m="hull"):
    part("cyl", parent, (x, y, h / 2), (r, r, h), m=m)
    part("sphere", parent, (x, y, h), (r, r, r * 0.6), m=m)
    part("torus", parent, (x, y, h * 0.6), (r * 1.02, r * 1.02, 1), m="accent", minor=0.08)


def tower(parent, h, r=0.4, light=True):
    part("cyl", parent, (0, 0, h / 2), (r, r, h), m="metal", v=12)
    for i in range(1, int(h // 3)):
        part("torus", parent, (0, 0, i * 3), (r * 2, r * 2, 1), m="metal", minor=0.06, v=16)
    if light:
        part("sphere", parent, (0, 0, h + 0.3), (0.4, 0.4, 0.4), m="light_red", v=12, rings=8)


def build_type(btype, root, rng, size_m):
    s = size_m
    if btype == "lander":
        part("cyl", root, (0, 0, 2.6), (1.8, 1.8, 2.2), m="hull", v=8)
        part("cone", root, (0, 0, 4.4), (1.8, 1.8, 1.4), m="hull", v=8, r2=0.4)
        for i in range(4):
            a = i * math.pi / 2 + math.pi / 4
            part("cyl", root, (math.cos(a) * 2.2, math.sin(a) * 2.2, 0.9), (0.12, 0.12, 2.2), m="metal",
                 rot=(math.sin(a) * 0.5, -math.cos(a) * 0.5, 0), v=8)
        part("torus", root, (0, 0, 2.0), (1.85, 1.85, 1), m="accent", minor=0.1, v=8)
    elif btype in ("hab_small", "medical_center"):
        dome(root, 2.8)
        if btype == "medical_center":
            part("cube", root, (0, 0, 3.6), (1.6, 0.4, 0.4), m="light_red")
            part("cube", root, (0, 0, 3.6), (0.4, 1.6, 0.4), m="light_red")
    elif btype == "solar_small":
        for i in range(3):
            for j in range(2):
                solar_panel(root, (i - 1) * 2.3, (j - 0.5) * 2.6)
    elif btype == "oxygen_plant":
        tank(root, -1.6, 0, 1.1, 4.5)
        tank(root, 1.6, 0, 1.1, 4.5)
        part("cube", root, (0, 2.2, 1.0), (4.5, 1.8, 2.0), m="hull")
        part("cyl", root, (0, 0, 3.0), (0.2, 0.2, 3.2), m="metal", rot=(0, math.pi / 2, 0), v=12)
        part("cube", root, (0, 3.15, 1.2), (3.0, 0.1, 0.4), m="light_cyan")
    elif btype == "dome_large":
        part("cyl", root, (0, 0, 0.4), (8.5, 8.5, 0.8), m="hull_dark", v=64)
        geodesic(root, 8.0)
        for i in range(6):
            a = i * math.pi / 3
            part("cube", root, (math.cos(a) * 3.5, math.sin(a) * 3.5, 1.5), (2, 2, 3), m="hull",
                 rot=(0, 0, a))
        part("sphere", root, (0, 0, 0.8), (1.2, 1.2, 1.2), m="plants")
        part("torus", root, (0, 0, 0.9), (8.6, 8.6, 1), m="light_cyan", minor=0.15, v=64)
    elif btype == "solar_farm":
        for i in range(6):
            for j in range(5):
                solar_panel(root, (i - 2.5) * 2.6, (j - 2) * 3.0, 2.3, 1.6)
        part("cube", root, (0, -7.8, 1), (3, 1.5, 2), m="hull")
    elif btype == "residential_complex":
        for i, (x, y) in enumerate(((-4, -3), (4, -3), (0, 4))):
            sub = empty(f"{root.name}_d{i}", (x, y, 0), root)
            dome(sub, 3.6, windows=True)
        part("cyl", root, (0, -3, 1.2), (0.9, 0.9, 8), m="hull", rot=(0, math.pi / 2, 0))
        part("cyl", root, (-2, 0.5, 1.2), (0.9, 0.9, 7.5), m="hull", rot=(0, math.pi / 2, math.radians(60)))
        sub = empty(f"{root.name}_t", (0, -0.5, 0), root)
        tower(sub, 14, 0.9)
        part("cube", sub, (0, 0, 10), (2.6, 2.6, 4), m="window")
    elif btype == "hq":
        part("cyl", root, (0, 0, 1), (9, 9, 2), m="hull_dark", v=64)
        part("cone", root, (0, 0, 14), (5, 5, 26), m="hull", v=48, r2=0.44)
        part("cyl", root, (0, 0, 28), (2.2, 2.2, 2), m="hull", v=32)
        part("torus", root, (0, 0, 18), (6.5, 6.5, 1), m="light_cyan", minor=0.25, v=64)
        part("torus", root, (0, 0, 18), (6.5, 6.5, 1), m="frame", minor=0.5, v=64, rot=(0, 0, 0))
        for i in range(12):
            part("cube", root, (0, 0, 4 + i * 2), (5.2 - i * 0.22, 0.2, 0.25), m="window", rot=(0, 0, i * 0.5))
        part("cyl", root, (0, 0, 31), (0.15, 0.15, 4), m="metal", v=8)
        part("sphere", root, (0, 0, 33.2), (0.6, 0.6, 0.6), m="light_red")
    elif btype in ("research_center", "research_lab"):
        k = 1.0 if btype == "research_center" else 0.55
        part("cube", root, (0, 0, 2.5 * k), (12 * k, 8 * k, 5 * k), m="hull")
        part("cube", root, (0, -4 * k - 0.05, 2.8 * k), (10 * k, 0.1, 1.0 * k), m="window")
        dish = empty(f"{root.name}_dish", (4 * k, 2 * k, 5 * k), root)
        part("cyl", dish, (0, 0, 2 * k), (0.3, 0.3, 4 * k), m="metal", v=12)
        part("sphere", dish, (0, 0, 4.5 * k), (3.2 * k, 3.2 * k, 1.0 * k), m="frame",
             rot=(math.radians(40), 0, 0))
        part("sphere", root, (-3 * k, 1 * k, 5 * k), (2.4 * k, 2.4 * k, 2.0 * k), m="glass")
    elif btype == "district":
        for i in range(3):
            for j in range(3):
                sub = empty(f"{root.name}_{i}{j}", ((i - 1) * 8.5, (j - 1) * 8.5, 0), root)
                if i == 1 and j == 1:
                    tower(sub, 18, 1.2)
                    part("cube", sub, (0, 0, 12), (3.4, 3.4, 8), m="window")
                else:
                    dome(sub, 2.8 + rng.random() * 0.6)
    elif btype == "mega_dome":
        geodesic(root, 70.0, thickness=0.9)
        part("torus", root, (0, 0, 0.5), (70.5, 70.5, 1), m="light_cyan", minor=0.6, v=128)
    elif btype == "spaceport":
        for i, (x, y) in enumerate(((-7, -7), (7, -7), (0, 7))):
            part("cyl", root, (x, y, 0.15), (5, 5, 0.3), m="hull_dark", v=48)
            part("torus", root, (x, y, 0.32), (4.6, 4.6, 1), m="ring_glow", minor=0.12, v=48)
        rocket = empty(f"{root.name}_rocket", (0, 7, 0.3), root)
        part("cyl", rocket, (0, 0, 9), (1.6, 1.6, 18), m="hull", v=32)
        part("cone", rocket, (0, 0, 20.5), (1.6, 1.6, 5), m="hull", v=32)
        part("torus", rocket, (0, 0, 12), (1.62, 1.62, 1), m="accent", minor=0.2, v=32)
        for i in range(4):
            a = i * math.pi / 2
            part("cube", rocket, (math.cos(a) * 1.9, math.sin(a) * 1.9, 2), (0.2, 1.4, 3.5), m="accent", rot=(0, 0, a))
        t = empty(f"{root.name}_tower", (-8, 8, 0), root)
        tower(t, 26, 0.8)
    elif btype == "second_city":
        for i in range(40):
            a, d = rng.random() * math.tau, rng.random() ** 0.5 * 45
            h = 10 + rng.random() * 45 * (1 - d / 55)
            part("cube", root, (math.cos(a) * d, math.sin(a) * d, h / 2), (5 + rng.random() * 4, 5 + rng.random() * 4, h),
                 m="hull" if i % 3 else "window")
        geodesic(root, 60.0, thickness=0.9, glass=False)
    elif btype == "landmark":
        for i in range(20):
            z = i * 3.2
            w = 6 * (1 - i / 22)
            part("cube", root, (0, 0, z + 1.6), (w, w, 3.0), m="hull" if i % 2 else "frame", rot=(0, 0, i * 0.18))
        part("cyl", root, (0, 0, 66), (0.5, 0.5, 6), m="light_cyan")
        part("torus", root, (0, 0, 40), (9, 9, 1), m="ring_glow", minor=0.25, v=64)
    elif btype == "greenhouse":
        part("cyl", root, (0, 0, 0), (2.8, 2.8, 7.0), m="glass", rot=(0, math.pi / 2, 0))
        for i in range(5):
            part("cube", root, ((i - 2) * 1.3, 0, 0.5), (1.0, 3.5, 1.0), m="plants")
        part("torus", root, (3.4, 0, 0), (2.85, 2.85, 1), m="frame", minor=0.12, rot=(0, math.pi / 2, 0))
        part("torus", root, (-3.4, 0, 0), (2.85, 2.85, 1), m="frame", minor=0.12, rot=(0, math.pi / 2, 0))
    elif btype == "water_extractor":
        sub = empty(f"{root.name}_t", (-1.5, 0, 0), root)
        tower(sub, 9, 0.5)
        tank(root, 2.0, 0, 1.5, 3.5, m="hull")
        part("cube", root, (2.0, 0, 3.2), (3.2, 0.12, 0.3), m="light_cyan")
    elif btype == "rover_garage":
        part("cube", root, (0, 0, 2), (7, 5, 4), m="hull")
        part("cube", root, (0, -2.55, 1.6), (5, 0.1, 3), m="hull_dark")
        part("cube", root, (0, -2.6, 3.4), (5.2, 0.1, 0.25), m="window")
    elif btype == "comm_tower":
        tower(empty(f"{root.name}_t", (0, 0, 0), root), 22, 0.5)
        part("sphere", root, (0, 0, 16), (2.2, 2.2, 0.7), m="frame", rot=(math.radians(50), 0, 0))
    elif btype == "mining_rig":
        part("cone", root, (0, 0, 5), (3.5, 3.5, 10), m="metal", v=4, r2=0.17)
        part("cube", root, (3, 0, 1.2), (3, 3, 2.4), m="accent")
        part("cyl", root, (0, 0, 2), (0.5, 0.5, 4), m="hull_dark")
    elif btype == "battery_bank":
        for i in range(4):
            part("cube", root, ((i - 1.5) * 1.7, 0, 1.1), (1.3, 3.5, 2.2), m="hull_dark")
            part("cube", root, ((i - 1.5) * 1.7, -1.8, 1.6), (0.9, 0.08, 0.15), m="light_green")
    elif btype == "observatory":
        part("cyl", root, (0, 0, 1.5), (3, 3, 3), m="hull")
        part("sphere", root, (0, 0, 3), (3, 3, 3), m="frame")
        part("cube", root, (0, 0, 4.5), (1.0, 6.2, 3.4), m="hull_dark", rot=(math.radians(20), 0, 0))
    else:
        dome(root, 2.5)


def building_world_pos(b):
    if b["pos_unit"] == "cell":
        return Vector((b["pos"][0] * CELL, b["pos"][1] * CELL, 0))
    return Vector((b["pos"][0], b["pos"][1], 0))


def size_of(b):
    return max(1, int(math.sqrt(len(b["cells"])))) * CELL if b["cells"] else 0


# --------------------------------------------------------------------------- Landschaft

def extent(world):
    cells = [c for b in world["buildings"] for c in b["cells"]]
    if not cells:
        return CELL * 3
    return (max(max(abs(c[0]), abs(c[1])) for c in cells) + 1.5) * CELL


def make_terrain(world, flat_r):
    rng = random.Random(world["seed"])
    craters = [(Vector((math.cos(a) * d, math.sin(a) * d)), rng.uniform(15, 60))
               for a, d in ((rng.random() * math.tau, rng.uniform(flat_r + 70, 650)) for _ in range(14))]
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=260, y_segments=260, size=800)
    for v in bm.verts:
        x, y = v.co.x, v.co.y
        d = math.hypot(x, y)
        t = min(1.0, max(0.0, (d - flat_r) / 70.0))
        t = t * t * (3 - 2 * t)
        h = noise.fractal(Vector((x / 90, y / 90, 0.3)), 1.0, 2.0, 5) * 7 * t
        h += noise.fractal(Vector((x / 15, y / 15, 1.7)), 1.0, 2.0, 3) * 0.6 * t
        far = min(1.0, max(0.0, (d - 300) / 350))
        h += max(0.0, noise.fractal(Vector((x / 220, y / 220, 5.1)), 0.8, 2.0, 4) + 0.3) * 90 * far
        for c, r in craters:
            cd = (Vector((x, y)) - c).length / r
            if cd < 1.4:
                h += (-(1 - cd * cd) * r * 0.18) if cd < 1 else (math.sin((cd - 1) / 0.4 * math.pi) * r * 0.05)
        v.co.z = h
    me = bpy.data.meshes.new("terrain")
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    o = bpy.data.objects.new("terrain", me)
    bpy.context.collection.objects.link(o)
    o.data.materials.append(terrain_material())
    return o


def scatter_rocks(world, flat_r):
    rng = random.Random(world["seed"] + 7)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1)
    proto = bpy.context.active_object
    for v in proto.data.vertices:
        v.co *= 0.75 + rng.random() * 0.5
    proto.data.materials.append(MATS["rock"])
    proto.location = (0, 0, -50)
    for i in range(260):
        a = rng.random() * math.tau
        d = rng.uniform(flat_r * 0.6, flat_r + 160)
        o = proto.copy()
        bpy.context.collection.objects.link(o)
        s = rng.uniform(0.2, 1.6) * (1 + d / 200)
        o.location = (math.cos(a) * d, math.sin(a) * d, 0)
        o.scale = (s, s * rng.uniform(0.7, 1.3), s * rng.uniform(0.4, 0.8))
        o.rotation_euler = (rng.random(), rng.random(), rng.random() * math.tau)


def make_roads(world):
    occ = {tuple(c) for b in world["buildings"] for c in b["cells"]}
    edges = set()
    for (x, y) in occ:
        corners = [(x - .5, y - .5), (x + .5, y - .5), (x + .5, y + .5), (x - .5, y + .5)]
        for i in range(4):
            a, b = corners[i], corners[(i + 1) % 4]
            edges.add(tuple(sorted((a, b))))
    bm = bmesh.new()
    hw = ROAD_W / 2
    for (a, b) in edges:
        ax, ay, bx, by = a[0] * CELL, a[1] * CELL, b[0] * CELL, b[1] * CELL
        if ay == by:
            pts = [(ax - hw, ay - hw), (bx + hw, ay - hw), (bx + hw, ay + hw), (ax - hw, ay + hw)]
        else:
            pts = [(ax - hw, ay - hw), (ax + hw, ay - hw), (ax + hw, by + hw), (ax - hw, by + hw)]
        vs = [bm.verts.new((px, py, 0.03)) for px, py in pts]
        bm.faces.new(vs)
    me = bpy.data.meshes.new("roads")
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new("roads", me)
    bpy.context.collection.objects.link(o)
    o.data.materials.append(MATS["road"])
    # Bauplatten: jede belegte Zelle bekommt ein Fundament (wirkt wie Häuserblocks).
    bm = bmesh.new()
    pad = (CELL - ROAD_W) / 2
    for (x, y) in occ:
        cx, cy = x * CELL, y * CELL
        vs = [bm.verts.new((cx + dx * pad, cy + dy * pad, 0.05)) for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        bm.faces.new(vs)
    me = bpy.data.meshes.new("pads")
    bm.to_mesh(me)
    bm.free()
    p = bpy.data.objects.new("pads", me)
    bpy.context.collection.objects.link(p)
    p.data.materials.append(MATS["pad"])
    # Randsteine/Lichter an Kreuzungen
    corners = {pt for e in edges for pt in e}
    for i, (cx, cy) in enumerate(sorted(corners)):
        if i % 2 == 0:
            part("cyl", None, (cx * CELL + 1.6, cy * CELL + 1.6, 0.6), (0.08, 0.08, 1.2), m="metal", v=6)
            part("sphere", None, (cx * CELL + 1.6, cy * CELL + 1.6, 1.25), (0.15, 0.15, 0.15), m="window", v=8, rings=4)
    nodes = sorted({p for e in edges for p in e})
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    return nodes, adj


def ring_road(radius):
    bpy.ops.mesh.primitive_torus_add(major_radius=radius, minor_radius=ROAD_W / 2,
                                     major_segments=160, minor_segments=6)
    o = bpy.context.active_object
    o.name = "ring_road"
    o.scale = (1, 1, 0.02)
    o.location = (0, 0, 0.0)
    o.data.materials.append(MATS["road"])
    return o


# --------------------------------------------------------------------------- Leben

def keyframe_loc(obj, frame, loc):
    obj.location = loc
    obj.keyframe_insert("location", frame=frame)


def colonists(world, nodes, adj, count, total_frames, fps):
    rng = random.Random(world["seed"] + 99)
    prefs = bpy.context.preferences.edit
    prefs.keyframe_new_interpolation_type = "LINEAR"
    bpy.ops.mesh.primitive_cylinder_add(vertices=10, radius=0.28, depth=1.2)
    body = bpy.context.active_object
    body.data.materials.append(MATS["suit"])
    body.location = (0, 0, -50)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=0.3)
    head = bpy.context.active_object
    head.data.materials.append(MATS["visor"])
    head.location = (0, 0, -50)
    if not nodes:
        return
    speed = 1.3  # m/s
    for i in range(count):
        root = empty(f"colonist_{i}")
        b = body.copy()
        bpy.context.collection.objects.link(b)
        b.parent, b.location = root, (0, 0, 0.6)
        h = head.copy()
        bpy.context.collection.objects.link(h)
        h.parent, h.location = root, (0, 0, 1.45)
        node = rng.choice(nodes)
        side = rng.choice((-0.6, 0.6))
        frame = 1.0 - rng.random() * 40
        prev = None
        while frame < total_frames + 1:
            pos = Vector((node[0] * CELL + side, node[1] * CELL + side, 0))
            keyframe_loc(root, frame, pos)
            nxt = [n for n in adj.get(node, []) if n != prev] or adj.get(node, [node])
            prev, node = node, rng.choice(nxt)
            frame += CELL / speed * fps * rng.uniform(0.9, 1.3)
            if rng.random() < 0.15:  # kurz stehen bleiben
                keyframe_loc(root, frame, pos)
                frame += fps * rng.uniform(1, 3)


def rovers(radius, total_frames, fps, count=3):
    prefs = bpy.context.preferences.edit
    prefs.keyframe_new_interpolation_type = "LINEAR"
    for i in range(count):
        pivot = empty(f"rover_pivot_{i}")
        r = empty(f"rover_{i}", (radius + (i % 2) * 0.0, 0, 0), pivot)
        part("cube", r, (0, 0, 1.0), (1.8, 3.4, 1.0), m="hull")
        part("cube", r, (0, 0.9, 1.6), (1.6, 1.2, 0.5), m="window")
        part("cube", r, (0, -1.6, 1.2), (1.9, 0.1, 0.3), m="light_red")
        for x in (-1, 1):
            for y in (-1.1, 1.1):
                part("cyl", r, (x * 1.0, y, 0.45), (0.45, 0.45, 0.3), m="hull_dark", rot=(0, math.pi / 2, 0), v=12)
        direction = 1 if i % 2 == 0 else -1
        r.rotation_euler = (0, 0, 0 if direction > 0 else math.pi)
        start = i * math.tau / count
        omega = 6.0 / radius * direction  # 6 m/s
        pivot.rotation_euler = (0, 0, start)
        pivot.keyframe_insert("rotation_euler", frame=1)
        pivot.rotation_euler = (0, 0, start + omega * total_frames / fps)
        pivot.keyframe_insert("rotation_euler", frame=total_frames + 1)


def build_animation(root, start, fps, light_strength=4000):
    prefs = bpy.context.preferences.edit
    prefs.keyframe_new_interpolation_type = "BEZIER"
    end = start + int(2.6 * fps)
    root.scale = (0.001, 0.001, 0.001)
    root.keyframe_insert("scale", frame=1)
    root.keyframe_insert("scale", frame=start)
    root.scale = (0.9, 0.9, 0.25)
    root.keyframe_insert("scale", frame=start + int(0.5 * fps))
    root.scale = (1.04, 1.04, 1.12)
    root.keyframe_insert("scale", frame=start + int(1.9 * fps))
    root.scale = (1, 1, 1)
    root.keyframe_insert("scale", frame=end)
    ld = bpy.data.lights.new(f"{root.name}_flash", "POINT")
    ld.color = (0.4, 0.85, 1.0)
    ld.shadow_soft_size = 4
    lo = bpy.data.objects.new(ld.name, ld)
    bpy.context.collection.objects.link(lo)
    lo.location = root.location + Vector((0, 0, 6))
    for f, e in ((1, 0), (start, 0), (start + int(0.4 * fps), light_strength), (end + fps, 0)):
        ld.energy = e
        ld.keyframe_insert("energy", frame=f)
    ring = part("torus", None, root.location + Vector((0, 0, 0.1)), (0.5, 0.5, 1), m="ring_glow", minor=0.08, v=64)
    for f, s in ((1, 0.001), (start, 0.001), (end, 18)):
        ring.scale = (s, s, 1)
        ring.keyframe_insert("scale", frame=f)
    ring.hide_render = True
    ring.keyframe_insert("hide_render", frame=1)
    ring.hide_render = False
    ring.keyframe_insert("hide_render", frame=start)
    ring.hide_render = True
    ring.keyframe_insert("hide_render", frame=end)


# --------------------------------------------------------------------------- Licht & Kamera

def lighting():
    sun_d = bpy.data.lights.new("sun", "SUN")
    sun_d.energy = 3.4
    sun_d.color = (1.0, 0.82, 0.62)
    sun_d.angle = math.radians(1.5)
    sun = bpy.data.objects.new("sun", sun_d)
    bpy.context.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(62), 0, math.radians(35))
    world = bpy.data.worlds.new("mars_sky")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes.get("Background")
    grad = nt.nodes.new("ShaderNodeTexGradient")
    grad.gradient_type = "LINEAR"
    mapping = nt.nodes.new("ShaderNodeMapping")
    coord = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    nt.links.new(coord.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    ramp.color_ramp.elements[0].position = 0.5
    ramp.color_ramp.elements[0].color = (0.85, 0.52, 0.32, 1)
    ramp.color_ramp.elements[1].position = 0.75
    ramp.color_ramp.elements[1].color = (0.32, 0.22, 0.24, 1)
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.9
    del grad, mapping


def make_camera(name, lens=50):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.clip_end = 3000
    cd.sensor_fit = "VERTICAL"
    cam = bpy.data.objects.new(name, cd)
    bpy.context.collection.objects.link(cam)
    target = empty(f"{name}_target")
    c = cam.constraints.new("TRACK_TO")
    c.target = target
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"
    return cam, target


def orbit_points(center, radius, height, a0, a1):
    return (center + Vector((math.cos(a0) * radius, math.sin(a0) * radius, height)),
            center + Vector((math.cos(a1) * radius, math.sin(a1) * radius, height)))


def animate_camera(shot, f0, f1, world, ext, buildings_by_id):
    prefs = bpy.context.preferences.edit
    prefs.keyframe_new_interpolation_type = "BEZIER"
    kind = shot["type"]
    cam, tgt = make_camera(f"cam_{shot['name']}", shot.get("lens", 40))
    center = Vector((0, 0, 0))
    a = math.radians(shot.get("angle", 225))
    if kind == "orbit":
        r, h = ext * 1.9 + 25, ext * 1.25 + 18
        p0, p1 = orbit_points(center, r, h, a - 0.35, a + 0.35)
        keys = [(f0, p0, center), (f1, p1, center)]
    elif kind == "flyto":
        b = buildings_by_id.get(shot.get("building"))
        bp = building_world_pos(b) if b else center
        sz = max(size_of(b) if b else CELL, 12)
        if b and b["type"] == "mega_dome":
            sz = 120
        if b and b["type"] == "second_city":
            sz = 110
        d0 = Vector((math.cos(a), math.sin(a), 0))
        start = bp + d0 * (sz * 4.5 + 40) + Vector((0, 0, sz * 2.6 + 25))
        end = bp + d0 * (sz * 1.9 + 8) + Vector((0, 0, sz * 0.9 + 5))
        look = bp + Vector((0, 0, sz * 0.25))
        keys = [(f0, start, look), (f1, end, look)]
    elif kind == "pan":
        r, h = ext * 1.1 + 14, ext * 0.7 + 10
        p0 = Vector((math.cos(a) * r, math.sin(a) * r, h))
        p1 = Vector((math.cos(a + 0.5) * r * 0.8, math.sin(a + 0.5) * r * 0.8, h * 0.8))
        keys = [(f0, p0, Vector((ext * 0.2, -ext * 0.1, 0))), (f1, p1, Vector((-ext * 0.2, ext * 0.15, 0)))]
    elif kind == "top":
        keys = [(f0, Vector((0.01, 0, ext * 2.6)), center), (f1, Vector((0.01, 0, ext * 2.6)), center)]
    elif kind == "pullout":
        p0 = Vector((math.cos(a) * ext * 1.0, math.sin(a) * ext * 1.0, ext * 0.8 + 12))
        p1 = Vector((math.cos(a - 0.2) * (ext * 3.0 + 60), math.sin(a - 0.2) * (ext * 3.0 + 60), ext * 2.0 + 50))
        keys = [(f0, p0, center), (f1, p1, center + Vector((0, 0, ext * 0.3)))]
    else:
        raise ValueError(kind)
    for f, pos, look in keys:
        cam.location = pos
        cam.keyframe_insert("location", frame=f)
        tgt.location = look
        tgt.keyframe_insert("location", frame=f)
    return cam


# --------------------------------------------------------------------------- Hauptprogramm

def main():
    a = args()
    t0 = time.time()
    world = json.load(open(a.world, encoding="utf-8"))
    plan = json.load(open(a.plan, encoding="utf-8"))
    fps = plan.get("fps", 24)
    scene = reset_scene()
    device = setup_render(scene, plan, a)
    init_materials()

    ext = extent(world)
    flat_r = ext + 30
    make_terrain(world, flat_r)
    scatter_rocks(world, flat_r)
    nodes, adj = make_roads(world)
    ring_r = ext + 12
    ring_road(ring_r)
    lighting()

    shots = plan["shots"]
    cursor = 1
    ranges = []
    for s in shots:
        n = max(1, round(s["seconds"] * fps))
        ranges.append((cursor, cursor + n - 1))
        cursor += n
    total = cursor - 1
    scene.frame_start, scene.frame_end = 1, total

    rng = random.Random(world["seed"] + 3)
    new_ids = set(plan.get("new_buildings", []))
    build_frame = None
    for s, (f0, _f1) in zip(shots, ranges):
        if s["name"] == plan.get("build_shot"):
            build_frame = f0 + int(s.get("build_delay", 1.2) * fps)
    by_id = {}
    for b in world["buildings"]:
        by_id[b["id"]] = b
        root = empty(f"bld_{b['id']}_{b['type']}", building_world_pos(b))
        root.rotation_euler = (0, 0, math.radians(b.get("rot", 0)))
        build_type(b["type"], root, rng, size_of(b))
        if b["id"] in new_ids and build_frame:
            build_animation(root, build_frame, fps)

    visible = int(plan.get("visible_colonists", 20))
    colonists(world, nodes, adj, visible, total, fps)
    rovers(ring_r, total, fps)

    cams = [animate_camera(s, f0, f1, world, ext, by_id) for s, (f0, f1) in zip(shots, ranges)]
    print(f"Szene aufgebaut in {time.time() - t0:.1f}s: {len(world['buildings'])} Gebäude, "
          f"{visible} Kolonisten, {total} Frames, Gerät {device}")
    if a.save_blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.save_blend))

    out = os.path.abspath(a.out)
    report = {"device": device, "fps": fps, "shots": []}
    for s, (f0, f1), cam in zip(shots, ranges, cams):
        scene.camera = cam
        folder = os.path.join(out, s["name"])
        os.makedirs(folder, exist_ok=True)
        if a.stills:
            mid = (f0 + f1) // 2
            scene.frame_set(mid)
            scene.render.filepath = os.path.join(folder, "still.jpg")
            bpy.ops.render.render(write_still=True)
        else:
            scene.frame_start, scene.frame_end = f0, f1
            scene.render.filepath = os.path.join(folder, "")
            ts = time.time()
            bpy.ops.render.render(animation=True)
            print(f"Einstellung {s['name']}: {f1 - f0 + 1} Frames in {time.time() - ts:.0f}s")
        report["shots"].append({"name": s["name"], "first": f0, "last": f1, "dir": folder})
    with open(os.path.join(out, "render_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"Fertig in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
