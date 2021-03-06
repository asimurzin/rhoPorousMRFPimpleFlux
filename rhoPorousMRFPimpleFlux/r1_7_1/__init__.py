#!/usr/bin/env python

#--------------------------------------------------------------------------------------
## pythonFlu - Python wrapping for OpenFOAM C++ API
## Copyright (C) 2010- Alexey Petrov
## Copyright (C) 2009-2010 Pebble Bed Modular Reactor (Pty) Limited (PBMR)
## 
## This program is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
## 
## You should have received a copy of the GNU General Public License
## along with this program.  If not, see <http://www.gnu.org/licenses/>.
## 
## See http://sourceforge.net/projects/pythonflu
##
## Author : Alexey PETROV
##


#---------------------------------------------------------------------------
def create_fields( runTime, mesh ):
    from Foam.OpenFOAM import ext_Info, nl
    ext_Info() << "Reading thermophysical properties\n" << nl
    
    from Foam.thermophysicalModels import basicPsiThermo, autoPtr_basicPsiThermo
    thermo = basicPsiThermo.New( mesh )

    p = thermo.p()
    h = thermo.h()
    psi = thermo.psi()
    
    from Foam.OpenFOAM import IOobject, word, fileName
    from Foam.finiteVolume import volScalarField
    rho = volScalarField( IOobject( word( "rho" ),
                                    fileName( runTime.timeName() ),
                                    mesh,
                                    IOobject.READ_IF_PRESENT,
                                    IOobject.AUTO_WRITE ),
                          thermo.rho() )

    ext_Info() << "Reading field U\n" << nl
    
    from Foam.finiteVolume import volVectorField
    U = volVectorField( IOobject( word( "U" ),
                                  fileName( runTime.timeName() ),
                                  mesh,
                                  IOobject.MUST_READ,
                                  IOobject.AUTO_WRITE ),
                        mesh )

    from Foam.finiteVolume.cfdTools.compressible import compressibleCreatePhi
    phi = compressibleCreatePhi( runTime, mesh, rho, U )

    from Foam.OpenFOAM import dimensionedScalar
    pMin = dimensionedScalar( mesh.solutionDict().subDict( word( "PIMPLE" ) ).lookup( word( "pMin" ) ) )

    ext_Info() << "Creating turbulence model\n" << nl
    from Foam import compressible
    turbulence = compressible.turbulenceModel.New( rho, U, phi, thermo() ) 

    # initialMass = fvc.domainIntegrate(rho)

    ext_Info() << "Creating field DpDt\n" << nl
    from Foam import fvc
    from Foam.finiteVolume import surfaceScalarField
    DpDt = fvc.DDt( surfaceScalarField( word( "phiU" ), phi / fvc.interpolate( rho ) ), p )

    from Foam.finiteVolume import MRFZones
    mrfZones = MRFZones( mesh )
    mrfZones.correctBoundaryVelocity( U )

    from Foam.finiteVolume import porousZones
    pZones = porousZones( mesh )
    
    from Foam.OpenFOAM import Switch
    pressureImplicitPorosity = Switch( False )
    
    return thermo, turbulence, p, h, psi, rho, U, phi, pMin, DpDt, mrfZones, pZones, pressureImplicitPorosity


#---------------------------------------------------------------------------
def fun_UEqn( mesh, pZones, rho, U, phi, turbulence, mrfZones, p, momentumPredictor, oCorr, nOuterCorr ):
    
    from Foam import fvm
    UEqn = pZones.ddt( rho, U ) + fvm.div( phi, U ) + turbulence.divDevRhoReff( U )
    
    if oCorr == nOuterCorr-1:
       UEqn.relax( 1.0 )
       pass
    else:
       UEqn.relax()
       pass
    
    mrfZones.addCoriolis( rho, UEqn )
    pZones.addResistance( UEqn )
    
    rUA = 1.0 / UEqn.A()
    
    if momentumPredictor:
       from Foam import fvc
       from Foam.finiteVolume import solve
       if oCorr == nOuterCorr-1:
          from Foam.OpenFOAM import word
          solve( UEqn == -fvc.grad( p ), mesh.solver( word( "UFinal" ) ) )
          pass
       else:
          solve( UEqn == -fvc.grad( p ) )
          pass
    else:
       U.ext_assign( rUA * ( UEqn.H() - fvc.grad( p ) ) )
       U.correctBoundaryConditions()
       pass
    
    return UEqn


#---------------------------------------------------------------------------
def fun_hEqn( mesh, rho, h, phi, turbulence, DpDt, thermo, oCorr, nOuterCorr ):
    from Foam import fvm
    hEqn = fvm.ddt(rho, h) + fvm.div( phi, h ) - fvm.laplacian( turbulence.alphaEff(), h ) == DpDt

    if oCorr == nOuterCorr-1:
       hEqn.relax()
       from Foam.OpenFOAM import word
       hEqn.solve(mesh.solver( word( "hFinal" ) ) )
       pass
    else:
       hEqn.relax()
       hEqn.solve()
       pass
    thermo.correct()
    pass
    
    return hEqn


#---------------------------------------------------------------------------
def fun_pEqn( mesh, thermo, p, rho, psi, U, phi, DpDt, pMin, UEqn, mrfZones, nNonOrthCorr, nCorr, oCorr, nOuterCorr, corr, transonic, cumulativeContErr ):
    
    rho.ext_assign( thermo.rho() )
    rUA = 1.0 / UEqn.A()
    U.ext_assign( rUA * UEqn.H() )
    
    if nCorr <= 1:
       UEqn.clear()
       pass
    
    if transonic:
       from Foam.finiteVolume import surfaceScalarField
       from Foam.OpenFOAM import word
       phid = surfaceScalarField( word( "phid" ),
                                  fvc.interpolate( psi ) * ( ( fvc.interpolate( U ) & mesh.Sf() ) + fvc.ddtPhiCorr( rUA, rho, U, phi ) ) )
       mrfZones.relativeFlux( fvc.interpolate( psi ), phid )

       from Foam import fvm
       for nonOrth in range( nNonOrthCorr + 1 ):
           pEqn = fvm.ddt( psi, p ) + fvm.div( phid, p ) - fvm.laplacian( rho * rUA, p )

           if oCorr == ( nOuterCorr-1 ) and ( corr == nCorr-1 ) and ( nonOrth == nNonOrthCorr ):
              from Foam.OpenFOAM import word
              pEqn.solve( mesh.solver( word( "pFinal" ) ) )
              pass
           else:
              pEqn.solve()
              pass

       if nonOrth == nNonOrthCorr:
          phi == pEqn.flux()
          pass
       
    else:
       from Foam import fvc
       phi.ext_assign( fvc.interpolate( rho ) * ( ( fvc.interpolate( U ) & mesh.Sf() ) ) )
       mrfZones.relativeFlux( fvc.interpolate( rho ), phi )
       
       from Foam import fvm
       for nonOrth in range( nNonOrthCorr + 1 ):
           # Pressure corrector
           pEqn = fvm.ddt( psi, p ) + fvc.div( phi ) - fvm.laplacian( rho * rUA, p )
           
           if oCorr == ( nOuterCorr-1 ) and corr == ( nCorr-1 ) and nonOrth == nNonOrthCorr: 
              from Foam.OpenFOAM import word
              pEqn.solve( mesh.solver( word( "pFinal" ) ) )
              pass
           else:
              pEqn.solve()
              pass
           
           if nonOrth == nNonOrthCorr:
              phi.ext_assign( phi + pEqn.flux() )
              pass
           pass
    
    
    from Foam.finiteVolume.cfdTools.compressible import rhoEqn
    rhoEqn( rho, phi )
    
    from Foam.finiteVolume.cfdTools.compressible import compressibleContinuityErrs
    cumulativeContErr = compressibleContinuityErrs( rho, thermo, cumulativeContErr )

    # Explicitly relax pressure for momentum corrector
    p.relax()

    rho.ext_assign( thermo.rho() )
    rho.relax()
    
    from Foam.OpenFOAM import ext_Info, nl
    ext_Info() << "rho max/min : " << rho.ext_max().value() << " " << rho.ext_min().value() << nl
    
    U.ext_assign( U - rUA * fvc.grad( p ) )
    U.correctBoundaryConditions()
    
    from Foam.finiteVolume import surfaceScalarField
    from Foam.OpenFOAM import word
    DpDt.ext_assign( fvc.DDt( surfaceScalarField( word( "phiU" ), phi / fvc.interpolate( rho ) ), p ) )
    
    from Foam.finiteVolume import bound
    bound( p, pMin )
    
    pass

#---------------------------------------------------------------------------
def main_standalone( argc, argv ):

    from Foam.OpenFOAM.include import setRootCase
    args = setRootCase( argc, argv )

    from Foam.OpenFOAM.include import createTime
    runTime = createTime( args )

    from Foam.OpenFOAM.include import createMesh
    mesh = createMesh( runTime )

    thermo, turbulence, p, h, psi, rho, U, phi, pMin, DpDt, mrfZones, pZones, pressureImplicitPorosity = create_fields( runTime, mesh )
    
    from Foam.finiteVolume.cfdTools.general.include import initContinuityErrs
    cumulativeContErr = initContinuityErrs()
    
    from Foam.OpenFOAM import ext_Info, nl
    ext_Info()<< "\nStarting time loop\n" << nl
    
    while runTime.run() :
        
        from Foam.finiteVolume.cfdTools.general.include import readTimeControls
        adjustTimeStep, maxCo, maxDeltaT = readTimeControls( runTime )
        
        from Foam.finiteVolume.cfdTools.general.include import readPIMPLEControls
        pimple, nOuterCorr, nCorr, nNonOrthCorr, momentumPredictor, transonic = readPIMPLEControls( mesh )
        
        from Foam.finiteVolume.cfdTools.compressible import compressibleCourantNo
        CoNum, meanCoNum = compressibleCourantNo( mesh, phi, rho, runTime )
        
        from Foam.finiteVolume.cfdTools.general.include import setDeltaT
        runTime = setDeltaT( runTime, adjustTimeStep, maxCo, maxDeltaT, CoNum )
        
        runTime.increment()
        
        ext_Info() << "Time = " << runTime.timeName() << nl << nl
        
        if nOuterCorr != 1:
           p.storePrevIter()
           rho.storePrevIter()
           pass
        
        from Foam.finiteVolume.cfdTools.compressible import rhoEqn
        rhoEqn( rho, phi )
        
        for oCorr in range( nOuterCorr ):
            UEqn = fun_UEqn( mesh, pZones, rho, U, phi, turbulence, mrfZones, p, momentumPredictor, oCorr, nOuterCorr  )
            hEqn = fun_hEqn( mesh, rho, h, phi, turbulence, DpDt, thermo, oCorr, nOuterCorr )
            
            for corr in range( nCorr ):
                fun_pEqn( mesh, thermo, p, rho, psi, U, phi, DpDt, pMin, UEqn, mrfZones, nNonOrthCorr, nCorr, oCorr, nOuterCorr, corr, transonic, cumulativeContErr )
                pass
            turbulence.correct()
            pass
        
        runTime.write()
        
        ext_Info() << "ExecutionTime = " << runTime.elapsedCpuTime() << " s" << "  ClockTime = " << runTime.elapsedClockTime() << " s" << nl << nl
        
        pass
    
    ext_Info() << "End\n"

    import os
    return os.EX_OK


#--------------------------------------------------------------------------------------
import sys, os
from Foam import FOAM_REF_VERSION
if FOAM_REF_VERSION( ">=", "010701" ):
   if __name__ == "__main__" :
      argv = sys.argv
      os._exit( main_standalone( len( argv ), argv ) )
      pass
   pass
else:
   from Foam.OpenFOAM import ext_Info
   ext_Info()<< "\nTo use this solver, It is necessary to SWIG OpenFoam1.7.1 or higher \n "


#--------------------------------------------------------------------------------------
