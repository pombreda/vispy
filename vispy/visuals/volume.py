# -*- coding: utf-8 -*-
# Copyright (c) 2014, Vispy Development Team.
# Distributed under the (new) BSD License. See LICENSE.txt for more info.

"""
About this technique
--------------------

In Python, we define the six faces of a cuboid to draw, as well as
texture cooridnates corresponding with the vertices of the cuboid. In
the vertex shader, we two positions along the view ray and pass both to
the fragment shader. In the fragment shader, we use these two points to
compute the ray direction and calculate the number of steps to use
in a for-loop while we iterate through the volume. Each iteration we
keep track of some voxel information. When the cast is done, we may do
some final processing. Depending on the render method, the calculations
at each iteration and the post-processing may differ.

It is important for the texture interpolation is 'linear', since with
nearest the result look very ugly. The wrapping should be clamp_to_edge
to avoid artifacts when the ray takes a small step outside the volume.

The ray direction is established by mapping the vertex to the document
coordinate frame, taking one step in z, and mapping the coordinate back.
The ray is expressed in coordinates local to the volume (i.e. texture
coordinates).

To calculate the number of steps, we calculate the distance between the
starting point and six planes corresponding to the faces of the cuboid.
The planes are placed slightly outside of the cuboid, resulting in sensible
output for rays that are faced in the wrong direction, allowing us to 
discard the front-facing sides of the cuboid by discarting fragments for
which the number of steps is very small.

"""

from ..gloo import Texture3D, TextureEmulated3D, VertexBuffer, IndexBuffer
from . import Visual
from .shaders import Function, ModularProgram
from ..color import get_colormap

import numpy as np

# todo: implement more render methods (port from visvis)
# todo: allow anisotropic data
# todo: what to do about lighting? ambi/diffuse/spec/shinynes on each visual?

# Vertex shader
VERT_SHADER = """
attribute vec3 a_position;
attribute vec3 a_texcoord;
uniform vec3 u_shape;

varying vec3 v_texcoord;
varying vec3 v_position;
varying vec4 v_position2;

void main() {
    v_texcoord = a_texcoord;
    v_position = a_position;
    
    // Project local vertex coordinate to camera position. Then do a step
    // backward (in cam coords) and project back. Voila, we get our ray vector.
    vec4 pos_in_cam1 = $viewtransformf(vec4(v_position, 1));

    // step backward
    vec4 pos_in_cam2 = pos_in_cam1 + vec4(0.0, 0.0, pos_in_cam1.w, 0);

    // position2 is another point along the view ray, closer to the camera.
    v_position2 = $viewtransformi(pos_in_cam2);
    
    gl_Position = $transform(vec4(v_position, 1.0));
}
"""  # noqa

# Fragment shader
FRAG_SHADER = """
// uniforms
uniform $sampler_type u_volumetex;
uniform vec3 u_shape;
uniform float u_threshold;
uniform float u_relative_step_size;

//varyings
varying vec3 v_texcoord;
varying vec3 v_position;
varying vec4 v_position2;

// uniforms for lighting. Hard coded until we figure out how to do lights
const vec4 u_ambient = vec4(0.2, 0.4, 0.2, 1.0);
const vec4 u_diffuse = vec4(0.8, 0.2, 0.2, 1.0);
const vec4 u_specular = vec4(1.0, 1.0, 1.0, 1.0);
const float u_shininess = 40.0;

//varying vec3 lightDirs[1];

// global holding view direction in local coordinates
vec3 view_ray;
// global defining near clipping plane in texture coordinates
vec4 clipplane;

vec4 calculateColor(vec4, vec3, vec3);
float rand(vec2 co);

void main() {{
    vec3 position2 = v_position2.xyz / v_position2.w;
    
    // Calculate unit vector pointing in the view direction through this 
    // fragment.
    view_ray = normalize(v_position.xyz - position2.xyz);

    // Calculate a clip plane (in texture coordinates) for the camera
    // position, in case that the camera is inside the volume.
    vec3 cameraposinvol = position2.xyz / u_shape;
    clipplane.xyz = view_ray;
    clipplane.w = dot(clipplane.xyz, cameraposinvol);
    
    // Get ray in texture coordinates
    vec3 ray = view_ray / u_shape;
    ray *= u_relative_step_size; // performance vs quality
    ray *= -1.0; // flip: we cast rays from back to front
    
    /// Get begin location and number of steps to cast ray
    vec3 edgeloc = v_texcoord;
    int nsteps = $calculate_steps(edgeloc, ray, clipplane);
    
    // Offset the ray with a random amount to make for a smoother
    // appearance when rotating the camera. noise is [0..1].
    float noise = rand((ray.xy * 10.0 + edgeloc.xy) * 100.0);
    edgeloc += ray * (0.5 - noise);
    
    // Instead of discarding based on gl_FrontFacing, we can also discard
    // on number of steps.
    if (nsteps < 4)
        discard;
    
    // For testing: show the number of steps. This helps to establish
    // whether the rays are correctly oriented
    //gl_FragColor = vec4(0.0, nsteps / 3.0 / u_shape.x, 1.0, 1.0);
    //return;
    
    {before_loop}
    
    // This outer loop seems necessary on some systems for large
    // datasets. Ugly, but it works ...
    int iter = nsteps;
    while (iter > 0) {{
        for (iter=iter; iter>0; iter--)
        {{
            // Calculate location and sample color
            vec3 loc = edgeloc + float(iter) * ray;
            vec4 color = $sample(u_volumetex, loc);
            float val = color.g;
            
            {in_loop}
        }}
    }}
    
    {after_loop}
    
    /* Set depth value - from visvis TODO
    int iter_depth = int(maxi);
    // Calculate end position in world coordinates
    vec4 position2 = vertexPosition;
    position2.xyz += ray*shape*float(iter_depth);
    // Project to device coordinates and set fragment depth
    vec4 iproj = gl_ModelViewProjectionMatrix * position2;
    iproj.z /= iproj.w;
    gl_FragDepth = (iproj.z+1.0)/2.0;
    */
}}


float rand(vec2 co)
{{
    // Create a pseudo-random number between 0 and 1.
    // http://stackoverflow.com/questions/4200224
    return fract(sin(dot(co.xy ,vec2(12.9898, 78.233))) * 43758.5453);
}}

float colorToVal(vec4 color1)
{{
    return color1.g; // todo: why did I have this abstraction in visvis?
}}

vec4 calculateColor(vec4 betterColor, vec3 loc, vec3 step)
{{   
    // Calculate color by incorporating lighting
    vec4 color1;
    vec4 color2;
    
    // View direction
    vec3 V = normalize(view_ray);
    
    // calculate normal vector from gradient
    vec3 N; // normal
    color1 = $sample( u_volumetex, loc+vec3(-step[0],0.0,0.0) );
    color2 = $sample( u_volumetex, loc+vec3(step[0],0.0,0.0) );
    N[0] = colorToVal(color1) - colorToVal(color2);
    betterColor = max(max(color1, color2),betterColor);
    color1 = $sample( u_volumetex, loc+vec3(0.0,-step[1],0.0) );
    color2 = $sample( u_volumetex, loc+vec3(0.0,step[1],0.0) );
    N[1] = colorToVal(color1) - colorToVal(color2);
    betterColor = max(max(color1, color2),betterColor);
    color1 = $sample( u_volumetex, loc+vec3(0.0,0.0,-step[2]) );
    color2 = $sample( u_volumetex, loc+vec3(0.0,0.0,step[2]) );
    N[2] = colorToVal(color1) - colorToVal(color2);
    betterColor = max(max(color1, color2),betterColor);
    float gm = length(N); // gradient magnitude
    N = normalize(N);
    
    // Flip normal so it points towards viewer
    float Nselect = float(dot(N,V) > 0.0);
    N = (2.0*Nselect - 1.0) * N;  // ==  Nselect * N - (1.0-Nselect)*N;
    
    // Get color of the texture (albeido)
    color1 = betterColor;
    color2 = color1;
    // todo: parametrise color1_to_color2
    
    // Init colors
    vec4 ambient_color = vec4(0.0, 0.0, 0.0, 0.0);
    vec4 diffuse_color = vec4(0.0, 0.0, 0.0, 0.0);
    vec4 specular_color = vec4(0.0, 0.0, 0.0, 0.0);
    vec4 final_color;
    
    // todo: allow multiple light, define lights on viewvox or subscene
    int nlights = 1; 
    for (int i=0; i<nlights; i++)
    {{ 
        // Get light direction (make sure to prevent zero devision)
        vec3 L = normalize(view_ray);  //lightDirs[i]; 
        float lightEnabled = float( length(L) > 0.0 );
        L = normalize(L+(1.0-lightEnabled));
        
        // Calculate lighting properties
        float lambertTerm = clamp( dot(N,L), 0.0, 1.0 );
        vec3 H = normalize(L+V); // Halfway vector
        float specularTerm = pow( max(dot(H,N),0.0), u_shininess);
        
        // Calculate mask
        float mask1 = lightEnabled;
        
        // Calculate colors
        ambient_color +=  mask1 * u_ambient;  // * gl_LightSource[i].ambient;
        diffuse_color +=  mask1 * lambertTerm;
        specular_color += mask1 * specularTerm * u_specular;
    }}
    
    // Calculate final color by componing different components
    final_color = color2 * ( ambient_color + diffuse_color) + specular_color;
    final_color.a = color2.a;
    
    // Done
    return final_color;
}}

"""  # noqa

# Code for calculating number of required steps
calc_steps = """

float d2P(vec3, vec3, vec4);

int calculate_steps(vec3 edgeLoc, vec3 ray, vec4 extra_clipplane)
{
    // Given the start pos, returns the number of steps towards the closest
    // face that is in front of the given ray.
    
    // Check for all six planes how many rays fit from the start point.
    // We operate in texture coordinate here (0..1)
    // Take the minimum value (not counting negative and invalid).
    float smallest = 999999.0;
    float eps = 0.000001;
    smallest = min(smallest, d2P(edgeLoc, ray, vec4(1.0, 0.0, 0.0, 0.0-eps)));
    smallest = min(smallest, d2P(edgeLoc, ray, vec4(0.0, 1.0, 0.0, 0.0-eps)));
    smallest = min(smallest, d2P(edgeLoc, ray, vec4(0.0, 0.0, 1.0, 0.0-eps)));
    smallest = min(smallest, d2P(edgeLoc, ray, vec4(1.0, 0.0, 0.0, 1.0+eps)));
    smallest = min(smallest, d2P(edgeLoc, ray, vec4(0.0, 1.0, 0.0, 1.0+eps)));
    smallest = min(smallest, d2P(edgeLoc, ray, vec4(0.0, 0.0, 1.0, 1.0+eps)));
    smallest = min(smallest, d2P(edgeLoc, ray, extra_clipplane));
    
    // Just in case, set extremely high value to 0
    smallest *= float(smallest < 10000.0);
    
    // Make int and return
    return int(smallest + 0.5);
}

float d2P(vec3 p, vec3 d, vec4 P)
{
    // calculate the distance of a point p to a plane P along direction d.
    // plane P is defined as ax + by + cz = d
    // return ~inf if negative
    
    // calculate nominator and denominator
    float nom = - (dot(P.xyz, p) - P.a);
    float denom =  dot(P.xyz, d);
    
    // Turn negative and invalid values into a very high value
    // A negative value means that the current face lies behind the ray. 
    // Invalid values can occur for combinations of face and start point
    float invalid = float(nom == 0.0 || denom == 0.0 || nom * denom <= 0.0);
    return (((1.0-invalid) * nom   + invalid * 999999.0) / 
            ((1.0-invalid) * denom + invalid * 1.0));
}
"""  # noqa


MIP_SNIPPETS = dict(
    before_loop="""
        float maxval = -99999.0; // The maximum encountered value
        float maxi = 0.0;  // Where the maximum value was encountered
        """,
    in_loop="""
        float r = float(val > maxval);
        maxval = (1.0 - r) * maxval + r * val;
        maxi = (1.0 - r) * maxi + r * float(iter);
        """,
    after_loop="""
        vec4 color = vec4(0.0);
        for (int i=0; i<5; i++) {
            float newi = maxi + 0.4 - 0.2 * float(i);
            color = max(color, $cmap($sample(u_volumetex,
                edgeloc + newi * ray).r));
        }
        gl_FragColor = color;
        """,
)

MIP_FRAG_SHADER = FRAG_SHADER.format(**MIP_SNIPPETS)

ISO_SNIPPETS = dict(
    before_loop="""
        vec4 color3 = vec4(0.0);  // final color
        vec3 step = 1.5 / u_shape;  // step to sample derivative
    """,
    in_loop="""
        float xxx = 0.5;
        if (val > u_threshold) {
            
            // Take the last stride in smaller steps
            for (int i=0; i<6; i++) {
                float newi = float(iter) + 1.0 - 0.2 * float(i);
                loc = edgeloc + newi * ray;
                val = $sample(u_volumetex, loc).r;

                if (val > u_threshold) {
                    color = $cmap(val);
                    gl_FragColor = calculateColor(color, loc, step);
                    return;
                }
            }
        }
        """,
    after_loop="""
        // If we get here, the ray did not meet the threshold
        discard;
        """,
)

ISO_FRAG_SHADER = FRAG_SHADER.format(**ISO_SNIPPETS)

frag_dict = {'mip': MIP_FRAG_SHADER, 'iso': ISO_FRAG_SHADER}


class VolumeVisual(Visual):
    """ Displays a 3D Volume
    
    Parameters
    ----------
    vol : ndarray
        The volume to display. Must be ndim==2.
    clim : tuple of two floats | None
        The contrast limits. The values in the volume are mapped to
        black and white corresponding to these values. Default maps
        between min and max.
    method : {'mip', 'iso'}
        The render method to use. See corresponding docs for details.
        Default 'mip'.
    threshold : float
        The threshold to use for the isosurafce render method. By default
        the mean of the given volume is used.
    relative_step_size : float
        The relative step size to step through the volume. Default 0.8.
        Increase to e.g. 1.5 to increase performance, at the cost of
        quality.
    cmap : str
        Colormap to use.
    emulate_texture : bool
        Use 2D textures to emulate a 3D texture. OpenGL ES 2.0 compatible,
        but has lower performance on desktop platforms.
    """

    def __init__(self, vol, clim=None, method='mip', threshold=None, 
                 relative_step_size=0.8, cmap='grays',
                 emulate_texture=False):
        Visual.__init__(self)
        tex_cls = TextureEmulated3D if emulate_texture else Texture3D

        # Storage of information of volume
        self._vol_shape = ()
        self._vertex_cache_id = ()
        self._clim = None      

        # Set the colormap
        self._cmap = get_colormap(cmap)

        # Create gloo objects
        self._vbo = None
        self._tex = tex_cls((10, 10, 10), interpolation='linear', 
                            wrapping='clamp_to_edge')

        # Create program
        self._program = ModularProgram(VERT_SHADER)
        self._program['u_volumetex'] = self._tex
        self._index_buffer = None
        
        # Set data
        self.set_data(vol, clim)
        
        # Set params
        self.method = method
        self.relative_step_size = relative_step_size
        self.threshold = threshold if (threshold is not None) else vol.mean()
    
    def set_data(self, vol, clim=None):
        """ Set the volume data. 
        """
        # Check volume
        if not isinstance(vol, np.ndarray):
            raise ValueError('Volume visual needs a numpy array.')
        if not ((vol.ndim == 3) or (vol.ndim == 4 and vol.shape[-1] <= 4)):
            raise ValueError('Volume visual needs a 3D image.')
        
        # Handle clim
        if clim is not None:
            clim = np.array(clim, float)
            if not (clim.ndim == 1 and clim.size == 2):
                raise ValueError('clim must be a 2-element array-like')
            self._clim = tuple(clim)
        if self._clim is None:
            self._clim = vol.min(), vol.max()
        
        # Apply clim
        vol = np.array(vol, dtype='float32', copy=False)
        vol -= self._clim[0]
        vol *= 1.0 / (self._clim[1] - self._clim[0])
        
        # Apply to texture
        self._tex.set_data(vol)  # will be efficient if vol is same shape
        self._program['u_shape'] = vol.shape[2], vol.shape[1], vol.shape[0]
        self._vol_shape = vol.shape[:3]
        
        # Create vertices?
        if self._index_buffer is None:
            self._create_vertex_data()
    
    @property
    def clim(self):
        """ The contrast limits that were applied to the volume data.
        Settable via set_data().
        """
        return self._clim
    
    @property
    def cmap(self):
        return self._cmap

    @cmap.setter
    def cmap(self, cmap):
        self._cmap = get_colormap(cmap)
        self._program.frag['cmap'] = Function(self._cmap.glsl_map)
        self.update()

    @property
    def method(self):
        """The render method to use

        Current options are:
        
            * mip: maxiumum intensity projection. Cast a ray and display the
              maximum value that was encountered.
            * iso: isosurface. Cast a ray until a certain threshold is
              encountered. At that location, lighning calculations are
              performed to give the visual appearance of a surface.  
        """
        return self._method
    
    @method.setter
    def method(self, method):
        # Check and save
        known_methods = ('mip', 'iso', 'ray')
        if method not in known_methods:
            raise ValueError('Volume render method should be in %r, not %r' %
                             (known_methods, method))
        self._method = method
        # Get rid of specific variables - they may become invalid
        self._program['u_threshold'] = None

        self._program.frag = frag_dict[method]
        self._program.frag['calculate_steps'] = Function(calc_steps)
        self._program.frag['sampler_type'] = self._tex.glsl_sampler_type
        self._program.frag['sample'] = self._tex.glsl_sample
        self._program.frag['cmap'] = Function(self._cmap.glsl_map)
        self.update()
    
    @property
    def threshold(self):
        """ The threshold value to apply for the isosurface render method.
        """
        return self._threshold
    
    @threshold.setter
    def threshold(self, value):
        self._threshold = float(value)
        self.update()
    
    @property
    def relative_step_size(self):
        """ The relative step size used during raycasting.
        
        Larger values yield higher performance at reduced quality. If
        set > 2.0 the ray skips entire voxels. Recommended values are
        between 0.5 and 1.5. The amount of quality degredation depends
        on the render method.
        """
        return self._relative_step_size
    
    @relative_step_size.setter
    def relative_step_size(self, value):
        value = float(value)
        if value < 0.1:
            raise ValueError('relative_step_size cannot be smaller than 0.1')
        self._relative_step_size = value
    
    def _create_vertex_data(self):
        """ Create and set positions and texture coords from the given shape
        
        We have six faces with 1 quad (2 triangles) each, resulting in
        6*2*3 = 36 vertices in total.
        """
        
        shape = self._vol_shape
        
        # Do we already have this or not?
        vertex_cache_id = self._vol_shape
        if vertex_cache_id == self._vertex_cache_id:
            return
        self._vertex_cache_id = None
        
        # Get corner coordinates. The -0.5 offset is to center
        # pixels/voxels. This works correctly for anisotropic data.
        x0, x1 = -0.5, shape[2] - 0.5
        y0, y1 = -0.5, shape[1] - 0.5
        z0, z1 = -0.5, shape[0] - 0.5

        data = np.empty(8, dtype=[
            ('a_position', np.float32, 3),
            ('a_texcoord', np.float32, 3)
        ])
        
        data['a_position'] = np.array([
            [x0, y0, z0],
            [x1, y0, z0],
            [x0, y1, z0],
            [x1, y1, z0],
            [x0, y0, z1],
            [x1, y0, z1],
            [x0, y1, z1],
            [x1, y1, z1],
        ], dtype=np.float32)
        
        data['a_texcoord'] = np.array([
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [1, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [0, 1, 1],
            [1, 1, 1],
        ], dtype=np.float32)
        
        """
          6-------7
         /|      /|
        4-------5 |
        | |     | |
        | 2-----|-3
        |/      |/
        0-------1
        """
        
        # Order is chosen such that the near faces of the volume are culled
        indices = np.array([2, 0, 6, 4, 5, 0, 1, 2, 3, 6, 7, 5, 3, 1], 
                           dtype=np.uint32)
        
        # Get some stats
        self._kb_for_texture = np.prod(self._vol_shape) / 1024
        self._kb_for_vertices = (indices.nbytes + data.nbytes) / 1024
        
        # Apply
        if self._vbo is not None:
            self._vbo.delete()
            self._index_buffer.delete()
        self._vbo = VertexBuffer(data)
        self._program.bind(self._vbo)
        self._index_buffer = IndexBuffer(indices)
        self._vertex_cache_id = vertex_cache_id
    
    def bounds(self, mode, axis):
        # Not sure if this is right. Do I need to take the transform if this
        # node into account?
        # Also, this method has no docstring, and I don't want to repeat
        # the docstring here. Maybe Visual implements _bounds that subclasses
        # can implement?
        return 0, self._vol_shape[2-axis]
    
    def draw(self, transforms):
        Visual.draw(self, transforms)
        
        full_tr = transforms.get_full_transform()
        self._program.vert['transform'] = full_tr
        self._program['u_relative_step_size'] = self._relative_step_size
        
        # Get and set transforms
        view_tr_f = transforms.visual_to_document
        view_tr_i = view_tr_f.inverse
        self._program.vert['viewtransformf'] = view_tr_f
        self._program.vert['viewtransformi'] = view_tr_i
        
        # Set attributes that are specific to certain methods
        self._program.build_if_needed()
        if self._method == 'iso':
            self._program['u_threshold'] = self._threshold
        
        # Draw!
        self._program.draw('triangle_strip', self._index_buffer)
