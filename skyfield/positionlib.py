"""Classes representing different kinds of astronomical position."""

from numpy import array, cos, einsum, exp, sin

from .constants import RAD2DEG, TAU, rotation_to_ecliptic
from .functions import dots, length_of, to_polar, rot_z
from .earthlib import compute_limb_angle, refract, terra
from .relativity import add_aberration, add_deflection
from .timelib import JulianDate, takes_julian_date
from .units import Distance, Velocity, Angle, _interpret_ltude


class ICRS(object):
    """An x,y,z position whose axes are oriented to the ICRS system.

    The ICRS is a permanent coordinate system that has superseded the
    old series of equinox-based systems like B1900, B1950, and J2000.

    """
    geocentric = True

    def __init__(self, position_AU, velocity_AU_per_d=None, jd=None):
        self.jd = jd
        self.position = Distance(position_AU)
        if velocity_AU_per_d is None:
            self.velocity = None
        else:
            self.velocity = Velocity(velocity_AU_per_d)

    def __repr__(self):
        return '<%s position x,y,z AU%s%s>' % (
            self.__class__.__name__,
            '' if (self.velocity is None) else
            ' and velocity xdot,ydot,zdot AU/day',
            '' if self.jd is None else ' at date jd',
            )

    def __sub__(self, body):
        """Subtract two ICRS vectors to produce a third."""
        p = self.position.AU - body.position.AU
        if self.velocity is None or body.velocity is None:
            v = None
        else:
            v = body.velocity.AU_per_d - self.velocity.AU_per_d
        return ICRS(p, v, self.jd)

    def distance(self):
        """Return the length of this vector.

        >>> v = ICRS([1.0, 1.0, 0.0])
        >>> print(v.distance())
        1.41421 AU

        """
        return Distance(length_of(self.position.AU))

    def radec(self, epoch=None):
        """Return this position as a tuple (RA, declination, distance).

        >>> ra, dec, distance = ICRS([1.0, 1.0, 1.0]).radec()
        >>> ra
        <Angle 03h 00m 00.00s>
        >>> dec
        <Angle +35deg 15' 51.8">
        >>> distance
        <Distance 1.73205 AU>

        """
        position_AU = self.position.AU
        if epoch is not None:
            if isinstance(epoch, JulianDate):
                pass
            elif isinstance(epoch, float):
                epoch = JulianDate(tt=epoch)
            elif epoch == 'date':
                epoch = self.jd
            else:
                raise ValueError('the epoch= must be a Julian date,'
                                 ' a floating point Terrestrial Time (TT),'
                                 ' or the string "date" for epoch-of-date')
            position_AU = einsum('ij...,j...->i...', epoch.M, position_AU)
        r_AU, dec, ra = to_polar(position_AU)
        return (Angle(radians=ra, preference='hours'),
                Angle(radians=dec, signed=True),
                Distance(r_AU))

    def ecliptic_position(self):
        """Return an x,y,z position relative to the ecliptic plane."""
        vector = rotation_to_ecliptic.dot(self.position.AU)
        return Distance(vector)

    def ecliptic_latlon(self):
        """Return ecliptic latitude, longitude, and distance."""
        vector = rotation_to_ecliptic.dot(self.position.AU)
        d, lat, lon = to_polar(vector)
        return (Angle(radians=lat, signed=True),
                Angle(radians=lon),
                Distance(AU=d))


class Topos(object):
    """An object representing a specific location on the Earth's surface."""

    def __init__(self, latitude=None, longitude=None, latitude_degrees=None,
                 longitude_degrees=None, elevation_m=0.0):

        if latitude_degrees is not None:
            latitude = Angle(degrees=latitude_degrees)
        elif isinstance(latitude, str):
            latitude = _interpret_ltude(latitude, 'latitude', 'N', 'S')
        elif not isinstance(latitude, Angle):
            raise TypeError('please provide either latitude_degrees=<float>'
                            ' or latitude=<skyfield.units.Angle object>'
                            ' with north being positive')

        if longitude_degrees is not None:
            longitude = Angle(degrees=longitude_degrees)
        elif isinstance(longitude, str):
            longitude = _interpret_ltude(longitude, 'longitude', 'E', 'W')
        elif not isinstance(longitude, Angle):
            raise TypeError('please provide either longitude_degrees=<float>'
                            ' or longitude=<skyfield.units.Angle object>'
                            ' with east being positive')

        self.latitude = latitude
        self.longitude = longitude
        self.elevation = Distance(m=elevation_m)

        lat = latitude.radians
        lon = longitude.radians
        sinlat = sin(lat)
        coslat = cos(lat)
        sinlon = sin(lon)
        coslon = cos(lon)

        self.up = array([coslat * coslon, coslat * sinlon, sinlat])
        self.north = array([-sinlat * coslon, -sinlat * sinlon, coslat])
        self.west = array([sinlon, -coslon, 0.0])

    @takes_julian_date
    def __call__(self, jd):
        """Compute where this Earth location was in space on a given date."""
        e = self.ephemeris.earth(jd)
        tpos_AU, tvel_AU_per_d = self._position_and_velocity(jd)
        t = Barycentric(e.position.AU + tpos_AU,
                        e.velocity.AU_per_d + tvel_AU_per_d,
                        jd)
        t.geocentric = False  # test, then get rid of this attribute
        t.rGCRS = tpos_AU
        t.vGCRS = tvel_AU_per_d
        t.topos = self
        t.ephemeris = self.ephemeris
        t.altaz_rotation = self._altaz_rotation(jd)
        return t

    @takes_julian_date
    def gcrs(self, jd):
        """Compute where this location was in the GCRS on a given date."""
        tpos_AU, tvel_AU_per_d = self._position_and_velocity(jd)
        t = Geocentric(tpos_AU, tvel_AU_per_d, jd)
        t.topos = self
        t.ephemeris = self.ephemeris
        t.altaz_rotation = self._altaz_rotation(jd)
        return t

    def _position_and_velocity(self, jd):
        """Return the GCRS position, velocity of this Topos at `jd`."""
        pos, vel = terra(self.latitude.radians, self.longitude.radians,
                         self.elevation.AU, jd.gast)
        pos = einsum('ij...,j...->i...', jd.MT, pos)
        vel = einsum('ij...,j...->i...', jd.MT, vel)
        return pos, vel

    def _altaz_rotation(self, jd):
        """Compute the rotation from the ICRS into the alt-az system."""
        spin = rot_z(jd.gast * TAU / 24.0)
        u = array([self.north, -self.west, self.up]).T
        spin_u = einsum('ij...,jk...->ki...', spin, u)
        return einsum('ij...,jk...->ik...', spin_u, jd.M)


class Barycentric(ICRS):
    """BCRS: an ICRS x,y,z position measured from the Solar System barycenter.

    """
    def observe(self, body):
        """Return the astrometric position of `body` viewed from this position.

        """
        return body._observe_from_bcrs(self)


class Astrometric(ICRS):
    """An astrometric position as an x,y,z vector in the ICRS.

    The *astrometric position* of a body is its position relative to an
    observer, adjusted for light-time delay: the position of the body
    back when it emitted (or reflected) the light that is now reaching
    the observer's eyes or telescope.  This is always a difference
    between two BCRS vectors.

    """
    def apparent(self):
        """Return the apparent position where this will appear in the sky.

        This method determines how relativity affects an image, and
        returns the :class:`~skyfield.positionlib.Apparent` position
        where the body will actually appear in the sky.  The effects
        modeled are the deflection that the image will experience if its
        light passes close to large masses in the Solar System, and the
        aberration caused by the observer's own velocity.

        These transforms convert the position from the BCRS reference
        frame of the Solar System barycenter and to the reference frame
        of the observer.  In the specific case of an Earth observer, the
        output reference frame is the GCRS.

        """
        jd = self.jd
        position_AU = self.position.AU.copy()
        observer = self.observer

        if observer.geocentric:
            include_earth_deflection = array((False,))
        else:
            limb_angle, nadir_angle = compute_limb_angle(
                position_AU, observer.position.AU)
            include_earth_deflection = limb_angle >= 0.8

        add_deflection(position_AU, observer.position.AU, observer.ephemeris,
                       jd.tdb, include_earth_deflection)
        add_aberration(position_AU, observer.velocity.AU_per_d, self.lighttime)

        a = Apparent(position_AU, jd=jd)
        a.observer = self.observer
        return a

class Apparent(ICRS):
    """An apparent position as an x,y,z vector in the GCRS.

    The *apparent position* of a body is its position relative to an
    observer, adjusted not only for the light-time delay between the
    body and an observer (which was already accounted for in the
    object's astrometric position), but also adjusted for deflection
    (its light rays bending as they pass large masses like the Sun or
    Jupiter) and aberration (light slanting because of the observer's
    motion through space).

    Included in aberration is the relativistic transformation that takes
    the position out of the BCRS centered on the solar system barycenter
    and into the GCRS centered on the Earth.

    If the observer was a planet or satellite with its own orbit around
    the Sun, then this apparent position is not really a GCRS position,
    but belongs to a GCRS-like system centered on that observer instead.

    """
    def altaz(self, temperature_C=None, pressure_mbar='standard'):
        """Return the position as a tuple ``(alt, az, distance)``.

        `alt` - Altitude in degrees above the horizon.
        `az` - Azimuth angle east around the horizon from due-north.
        `distance` - Distance to the object.

        """
        try:
            topos = self.observer.topos
            R = self.observer.altaz_rotation
        except AttributeError:
            raise ValueError('to compute an apparent position, you must'
                             ' observe from a specific Earth location that'
                             ' you specify using a Topos instance')

        # TODO: wobble

        position_AU = einsum('ij...,j...->i...', R, self.position.AU)
        r_AU, alt, az = to_polar(position_AU)

        if temperature_C is None:
            alt = Angle(radians=alt)
        else:
            if temperature_C == 'standard':
                temperature_C = 10.0
            if pressure_mbar == 'standard':
                pressure_mbar = 1010.0 * exp(-topos.elevation.m / 9.1e3)
            alt = refract(alt * RAD2DEG, temperature_C, pressure_mbar)
            alt = Angle(degrees=alt)

        return alt, Angle(radians=az), Distance(r_AU)

    # @classmethod
    # def from_altaz(cls, alt_degrees, az_degrees):
    #     r = 0.1  # close enough to make gravitational refraction irrelevant
    #     return cls(from_polar(r, alt_degrees, az_degrees))

class Geocentric(ICRS):
    """A position referred to the GCRS as measured from the geocenter."""

    def observe(self, other):
        gcrs_method = getattr(other, 'gcrs')
        if gcrs_method is None:
            raise ValueError('currently a Geocentric location can only'
                             ' observe an object that can generate a'
                             ' GCRS position through a .gcrs() method')
        g = gcrs_method(self.jd)
        # TODO: light-travel-time backdating, if distant enough?
        p = g.position.AU - self.position.AU
        v = g.velocity.AU_per_d - self.velocity.AU_per_d
        a = Apparent(p, v, self.jd)
        a.observer = self
        return a

def ITRF_to_GCRS(jd, rITRF):  # todo: velocity

    # Todo: wobble

    spin = rot_z(jd.gast * TAU / 24.0)
    position = einsum('ij...,j...->i...', spin, array(rITRF))
    return einsum('ij...,j...->i...', jd.MT, position)
