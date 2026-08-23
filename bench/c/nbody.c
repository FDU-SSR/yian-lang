#include <stdio.h>
#include <stdlib.h>
#include <math.h>

typedef struct planet {
    double x, y, z;
    double vx, vy, vz;
    double mass;
} planet;

int advance(int nbodies, planet* bodies, double dt) {
    for (int i = 0; i < nbodies; ++i) {
        planet b = bodies[i];
        for (int j = i + 1; j < nbodies; ++j) {
            planet b2 = bodies[j];

            double dx = b.x - b2.x;
            double dy = b.y - b2.y;
            double dz = b.z - b2.z;

            double distanced = dx * dx + dy * dy + dz * dz;
            double distance = sqrt(distanced);
            double mag = dt / (distanced * distance);

            b.vx -= dx * b2.mass * mag;
            b.vy -= dy * b2.mass * mag;
            b.vz -= dz * b2.mass * mag;

            b2.vx += dx * b.mass * mag;
            b2.vy += dy * b.mass * mag;
            b2.vz += dz * b.mass * mag;

            bodies[j] = b2;
        }
        bodies[i] = b;
    }

    for (int i = 0; i < nbodies; ++i) {
        planet b = bodies[i];
        b.x += b.vx * dt;
        b.y += b.vy * dt;
        b.z += b.vz * dt;
        bodies[i] = b;
    }
    return 0;
}


double energy(int nbodies, planet* bodies) {
    double e = 0.0;
    for (int i = 0; i < nbodies; ++i) {
        planet b = bodies[i];

        e += 0.5 * b.mass * (b.vx * b.vx + b.vy * b.vy + b.vz * b.vz);

        for (int j = i + 1; j < nbodies; ++j) {
            planet b2 = bodies[j];

            double dx = b.x - b2.x;
            double dy = b.y - b2.y;
            double dz = b.z - b2.z;

            double distance = sqrt(dx * dx + dy * dy + dz * dz);


            e -= (b.mass * b2.mass) / distance;
        }
    }
    return e;
}


int offset_momentum(int nbodies, planet* bodies) {
    double px = 0.0, py = 0.0, pz = 0.0;
    double pi = 3.141592653589793;
    double solar_mass = 4 * pi * pi;

    for (int i = 0; i < nbodies; ++i) {
        planet b = bodies[i];
        px += b.vx * b.mass;
        py += b.vy * b.mass;
        pz += b.vz * b.mass;
    }

    planet b = bodies[0];
    b.vx = -px / solar_mass;
    b.vy = -py / solar_mass;
    b.vz = -pz / solar_mass;
    bodies[0] = b;

    return 0;
}


void init_bodies(planet* bodies) {

    double pi = 3.141592653589793;
    double solar_mass = 4 * pi * pi;
    double days_per_year = 365.21;


    bodies[0] = (planet){0.0, 0.0, 0.0, 0.0, 0.0, 0.0, solar_mass};
    bodies[1] = (planet){
        4.84143144246472090e+00, -1.16032004402742839e+00, -1.03622044471123109e-01,
        1.66007664274403694e-03 * days_per_year, 7.69901118419740425e-03 * days_per_year, -6.90460016972063023e-05 * days_per_year,
        9.54791938424326609e-04 * solar_mass
    };
    bodies[2] = (planet){
        8.34336671824457987e+00, 4.12479856412430479e+00, -4.03523417114321381e-01,
        -2.76742510726862411e-03 * days_per_year, 4.99852801234917238e-03 * days_per_year, 2.30417297573763929e-05 * days_per_year,
        2.85885980666130812e-04 * solar_mass
    };
    bodies[3] = (planet){
        1.28943695621391310e+01, -1.51111514016986312e+01, -2.23307578892655734e-01,
        2.96460137564761618e-03 * days_per_year, 2.37847173959480950e-03 * days_per_year, -2.96589568540237556e-05 * days_per_year,
        4.36624404335156298e-05 * solar_mass
    };
    bodies[4] = (planet){
        1.53796971148509165e+01, -2.59193146099879641e+01, 1.79258772950371181e-01,
        2.68067772490389322e-03 * days_per_year, 1.62824170038242295e-03 * days_per_year, -9.51592254519715870e-05 * days_per_year,
        5.15138902046611451e-05 * solar_mass
    };
}

int main() {
    int n = 12000000;
    double s1 = 0.0, s2 = 0.0;

    planet bodies[5];

    init_bodies(bodies);
    offset_momentum(5, bodies);

    s1 = energy(5, bodies);

    for (int i = 0; i < n; ++i) {
        advance(5, bodies, 0.01);
    }

    s2 = energy(5, bodies);

    int result = (int)((s2 - s1) * 100000.0);

    printf("%d\n", result);

    return 0;
}