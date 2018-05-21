# Copyright (c) 2012-2018 by the GalSim developers team on GitHub
# https://github.com/GalSim-developers
#
# This file is part of GalSim: The modular galaxy image simulation toolkit.
# https://github.com/GalSim-developers/GalSim
#
# GalSim is free software: redistribution and use in source and binary forms,
# with or without modification, are permitted provided that the following
# conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions, and the disclaimer given in the accompanying LICENSE
#    file.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions, and the disclaimer given in the documentation
#    and/or other materials provided with the distribution.
#

"""Script to validate the geometric approximation "second kick", and determine which cutoff scales
may be appropriate for separating turbulent scales.  Since the diffractive approximation second kick
is supposed to be a good match to the PSF generated by high-k turbulence, we should be able to
compare it (including an implicit convolution with an Airy profile) with the full Fourier optics
calculation if we restrict the phase power in the latter calculation to only high-k modes.  Note
that since the second kick is an expectation value, we only expect good agreement for long exposure
times.

This script makes plots of the full Fourier PSF with truncated scales in the top row, and the
refractive approximation second kick including Airy in the bottom row.  A different cutoff scale is
used in each column.  Each panel has inlaid the HSM-measured PSF size sigma.

We find generally good agreement between the analytic calculation and the Fourier optics simulation
over a fairly wide range of kcrit from 0.05 to 0.5, which produce surface brightness profiles
spanning a range in size (HSM-sigma) from 4.8 to 0.7 pixels.
"""

import os
import numpy as np
import galsim

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from astropy.utils.console import ProgressBar

def make_plot(args):
    # Initiate some GalSim random number generators.
    rng = galsim.BaseDeviate(args.seed)
    u = galsim.UniformDeviate(rng)

    # The GalSim atmospheric simulation code describes turbulence in the 3D atmosphere as a series
    # of 2D turbulent screens.  The galsim.Atmosphere() helper function is useful for constructing
    # this screen list.

    # First, we estimate a weight for each screen, so that the turbulence is dominated by the lower
    # layers consistent with direct measurements.  The specific values we use are from SCIDAR
    # measurements on Cerro Pachon as part of the 1998 Gemini site selection process
    # (Ellerbroek 2002, JOSA Vol 19 No 9).

    Ellerbroek_alts = [0.0, 2.58, 5.16, 7.73, 12.89, 15.46]  # km
    Ellerbroek_weights = [0.652, 0.172, 0.055, 0.025, 0.074, 0.022]
    Ellerbroek_interp = galsim.LookupTable(Ellerbroek_alts, Ellerbroek_weights,
                                           interpolant='linear')

    # Use given number of uniformly spaced altitudes
    alts = np.max(Ellerbroek_alts)*np.arange(args.nlayers)/(args.nlayers-1)
    weights = Ellerbroek_interp(alts)  # interpolate the weights
    weights /= sum(weights)  # and renormalize

    # Each layer can have its own turbulence strength (roughly inversely proportional to the Fried
    # parameter r0), wind speed, wind direction, altitude, and even size and scale (though note that
    # the size of each screen is actually made infinite by "wrapping" the edges of the screen.)  The
    # galsim.Atmosphere helper function is useful for constructing this list, and requires lists of
    # parameters for the different layers.

    spd = []  # Wind speed in m/s
    dirn = [] # Wind direction in radians
    r0_500 = [] # Fried parameter in m at a wavelength of 500 nm.
    for i in range(args.nlayers):
        spd.append(u()*args.max_speed)  # Use a random speed between 0 and max_speed
        dirn.append(u()*360*galsim.degrees)  # And an isotropically distributed wind direction.
        # The turbulence strength of each layer is specified by through its Fried parameter r0_500,
        # which can be thought of as the diameter of a telescope for which atmospheric turbulence
        # and unaberrated diffraction contribute equally to image resolution (at a wavelength of
        # 500nm).  The weights above are for the refractive index structure function (similar to a
        # variance or covariance), however, so we need to use an appropriate scaling relation to
        # distribute the input "net" Fried parameter into a Fried parameter for each layer.  For
        # Kolmogorov turbulence, this is r0_500 ~ (structure function)**(-3/5):
        r0_500.append(args.r0_500*weights[i]**(-3./5))
        print("Adding layer at altitude {:5.2f} km with velocity ({:5.2f}, {:5.2f}) m/s, "
              "and r0_500 {:5.3f} m."
              .format(alts[i], spd[i]*dirn[i].cos(), spd[i]*dirn[i].sin(), r0_500[i]))

    # Apply fudge factor
    r0_500 = [r*args.turb_factor**(-3./5) for r in r0_500]

    # Start output at this point
    fig, axes = plt.subplots(nrows=2, ncols=4, figsize=(8, 5))
    FigureCanvasAgg(fig)
    for ax in axes.ravel():
        ax.set_xticks([])
        ax.set_yticks([])

    kcrits = np.logspace(np.log10(args.kmin), np.log10(args.kmax), 4)
    r0 = args.r0_500*(args.lam/500.0)**(6./5)
    for icol, kcrit in enumerate(kcrits):
        # Make sure to use a consistent seed for the atmosphere when varying kcrit
        # Additionally, we set the screen size and scale.
        atmRng = galsim.BaseDeviate(args.seed+1)
        print("Inflating atmosphere with kcrit={}".format(kcrit))
        atm = galsim.Atmosphere(r0_500=r0_500, L0=args.L0,
                                speed=spd, direction=dirn, altitude=alts, rng=atmRng,
                                screen_size=args.screen_size, screen_scale=args.screen_scale)
        with ProgressBar(args.nlayers) as bar:
            atm.instantiate(kmin=kcrit/r0, _bar=bar)
        print(atm[0].screen_scale, atm[0].screen_size)
        print(atm[0]._tab2d.f.shape)

        # Construct an Aperture object for computing the PSF.  The Aperture object describes the
        # illumination pattern of the telescope pupil, and chooses good sampling size and resolution
        # for representing this pattern as an array.
        aper = galsim.Aperture(diam=args.diam, lam=args.lam, obscuration=args.obscuration,
                               screen_list=atm, pad_factor=args.pad_factor,
                               oversampling=args.oversampling)

        print("Drawing with Fourier optics")
        with ProgressBar(args.exptime/args.time_step) as bar:
            psf = atm.makePSF(lam=args.lam, aper=aper, exptime=args.exptime,
                              time_step=args.time_step, _bar=bar)
            fftImg = psf.drawImage(nx=args.nx, ny=args.nx, scale=args.scale)

        secondKick = galsim.SecondKick(lam=args.lam, r0=args.r0_500*(args.lam/500.)**(6./5),
                                        diam=args.diam, obscuration=args.obscuration,
                                       kcrit=kcrit)
        secondKickImg = secondKick.drawImage(nx=args.nx, ny=args.nx, scale=args.scale,
                                             method='phot', n_photons=args.nphot)

        try:
            fftMom = galsim.hsm.FindAdaptiveMom(fftImg)
        except RuntimeError:
            fftMom = None

        try:
            secondKickMom = galsim.hsm.FindAdaptiveMom(secondKickImg)
        except RuntimeError:
            secondKickMom = None

        axes[0, icol].imshow(fftImg.array)
        axes[1, icol].imshow(secondKickImg.array)

        if fftMom is not None:
            axes[0, icol].text(0.5, 0.9, "{:6.3f}".format(fftMom.moments_sigma),
                               transform=axes[0, icol].transAxes, color='w')

        if secondKickMom is not None:
            axes[1, icol].text(0.5, 0.9, "{:6.3f}".format(secondKickMom.moments_sigma),
                               transform=axes[1, icol].transAxes, color='w')
        axes[0, icol].set_title("{:4.2f}".format(kcrit))

    axes[0, 0].set_ylabel("FFT")
    axes[1, 0].set_ylabel("2nd kick")

    fig.tight_layout()

    dirname, filename = os.path.split(args.outfile)
    if not os.path.exists(dirname):
        os.mkdir(dirname)
    fig.savefig(args.outfile)


if __name__ == '__main__':
    from argparse import ArgumentParser
    parser = ArgumentParser()

    parser.add_argument("--seed", type=int, default=1,
                        help="Random number seed for generating turbulence.  Default: 1")
    parser.add_argument("--r0_500", type=float, default=0.15,
                        help="Fried parameter at wavelength 500 nm in meters.  Default: 0.15")
    parser.add_argument("--turb_factor", type=float, default=1.0,
                        help="Turbulence fudge factor.  Default: 1.0")
    parser.add_argument("--L0", type=float, default=25.0,
                        help="Outer scale in meters.  Default: 25.0")
    parser.add_argument("--nlayers", type=int, default=6,
                        help="Number of atmospheric layers.  Default: 6")
    parser.add_argument("--time_step", type=float, default=0.025,
                        help="Incremental time step for advancing phase screens and accumulating "
                             "instantaneous PSFs in seconds.  Default: 0.025")
    parser.add_argument("--exptime", type=float, default=30.0,
                        help="Total amount of time to integrate in seconds.  Default: 30.0")
    parser.add_argument("--screen_size", type=float, default=102.4,
                        help="Size of atmospheric screen in meters.  Note that the screen wraps "
                             "with periodic boundary conditions.  Default: 102.4")
    parser.add_argument("--screen_scale", type=float, default=0.0125,
                        help="Resolution of atmospheric screen in meters.  Default: 0.0125")
    parser.add_argument("--max_speed", type=float, default=20.0,
                        help="Maximum wind speed in m/s.  Default: 20.0")
    parser.add_argument("--kmin", type=float, default=0.05,
                        help="Minimum kcrit to plot.  Default: 0.05")
    parser.add_argument("--kmax", type=float, default=0.5,
                        help="Maximum kcrit to plot.  Default: 0.5")
    parser.add_argument("--nphot", type=int, default=int(3e6),
                        help="Number of photons to shoot.  Default: 3e6")

    parser.add_argument("--lam", type=float, default=700.0,
                        help="Wavelength in nanometers.  Default: 700.0")
    parser.add_argument("--diam", type=float, default=8.36,
                        help="Size of circular telescope pupil in meters.  Default: 8.36")
    parser.add_argument("--obscuration", type=float, default=0.61,
                        help="Linear fractional obscuration of telescope pupil.  Default: 0.61")

    parser.add_argument("--nx", type=int, default=64,
                        help="Output PSF image dimensions in pixels.  Default: 64")
    parser.add_argument("--scale", type=float, default=0.04,
                        help="Scale of PSF output pixels in arcseconds.  Default: 0.04")

    parser.add_argument("--pad_factor", type=float, default=1.0,
                        help="Factor by which to pad PSF InterpolatedImage to avoid aliasing. "
                             "Default: 1.0")
    parser.add_argument("--oversampling", type=float, default=1.0,
                        help="Factor by which to oversample the PSF InterpolatedImage. "
                             "Default: 1.0")

    parser.add_argument("--outfile", type=str, default="output/secondKick.png",
                        help="Output filename.  Default: output/secondKick.png")

    args = parser.parse_args()
    make_plot(args)
