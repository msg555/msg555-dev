#include <iostream>
#include <vector>
#include <algorithm>
#include <cassert>
#include <cmath>

using namespace std;

#define EPS ((long double)1e-9)
typedef long double num;

inline num nabs(num x) {
  return x < 0 ? -x : x;
}

bool num_lt(num X, num Y) {
  return X + max(num(1), nabs(Y)) * EPS < Y;
}

bool num_lteq(num X, num Y) {
  return X <= Y + max(num(1), nabs(Y)) * EPS;
}

bool num_eq(num X, num Y) {
  return num_lteq(X, Y) && num_lteq(Y, X);
}

struct simplex {
  simplex(int V) : V(V), slack(0), feasible(false), unbounded(false), value(0) {
  }

  void halfspace(const vector<num>& a, num b) {
    assert(a.size() == V);
    A.push_back(a);
    B.push_back(b);
    equal.push_back(false);
    slack += num_lt(b, 0) ? 2 : 1;
  }

  void plane(const vector<num>& a, num b) {
    assert(a.size() == V);
    A.push_back(a);
    B.push_back(b);
    equal.push_back(true);
    slack++;
  }

  void pivot_column(vector<vector<num> >& T, int rows, int cols, int pc) {
    /* Select pivot row */
    int pr = -1;
    for (int i = 0; i < rows; i++) {
      if (num_lteq(T[i][pc], 0)) {
        continue;
      }
      if (pr == -1 || T[i][cols] / T[i][pc] < T[pr][cols] / T[pr][pc]) {
        pr = i;
      }
    }
    assert(pr != -1);

    /* Perform pivot */
    for (int i = 0; i <= rows + 1; i++) {
      if (i == pr) {
        continue;
      }
      num r = T[i][pc] / T[pr][pc];
      for (int j = 0; j <= cols; j++) {
        if (num_eq(T[i][j], r * T[pr][j])) {
          T[i][j] = 0;
        } else {
          T[i][j] -= r * T[pr][j];
        }
      }
    }
  }

  void solve(const vector<num>& C) {
    int rows = A.size();
    int cols = V + slack + 1;
    vector<vector<num> > T(rows + 2, vector<num>(cols + 1));

    /* Initialize table */
    int sp = V;
    vector<int> slacks;
    for (int i = 0; i < rows; i++) {
      for (int j = 0; j < V; j++) {
        T[i][j] = A[i][j];
      }
      if (equal[i]) {
        slacks.push_back(sp);
        T[i][sp] = num_eq(0, B[i]) ? 1 : B[i];
        T[rows + 1][sp] = 1;
        ++sp;
      } else {
        if (num_lt(B[i], 0)) {
          slacks.push_back(sp);
          T[i][sp] = B[i];
          T[rows + 1][sp] = 1;
          ++sp;
        }
        T[i][sp] = 1;
        ++sp;
      }
      T[i][cols] = B[i];
    }
    for (int i = 0; i < V; i++) {
      T[rows][i] = -C[i];
    }
    T[rows][cols - 1] = 1;
    T[rows + 1][cols - 1] = 1;

    unbounded = false;
    value = 0;
    solution.clear();
    for (;;) {
      /* Select pivot column */
      int pc = -1;
      if (slacks.empty()) {
        for (int i = 0; i < cols; i++) {
          if (num_lt(0, T[rows + 1][i]) ||
              (num_eq(0, T[rows + 1][i]) && num_lteq(0, T[rows][i]))) {
            continue;
          }
          if (pc == -1 ||
              num_lt(T[rows + 1][i], T[rows + 1][pc]) ||
              (num_eq(T[rows + 1][i], T[rows + 1][pc]) &&
               num_lt(T[rows][i], T[rows][pc]))) {
            pc = i;
          }
        }
      } else {
        pc = slacks.back();
        slacks.pop_back();
      }
      if (pc == -1) {
        break;
      }
      pivot_column(T, rows, cols, pc);

#if 0
      // What is this?
      for (int i = 0; i < cols; i++) {
        num mx = 0;
        num mn = 1e300;
        for (int j = 0; j <= rows + 1; j++) {
          if (num_eq(T[j][i], 0)) {
            T[j][i] = 0;
          } else {
            mx = max(nabs(T[j][i]), mx);
            mn = min(nabs(T[j][i]), mn);
          }
        }
        if (num_eq(mx, 0)) {
          continue;
        }
        num div = sqrt(mn * mx);
        for (int j = 0; j <= rows + 1; j++) {
          T[j][i] /= div;
        }
      }
#endif
    }

    feasible = num_eq(T[rows + 1][cols] / T[rows][cols - 1], 0);
    if (!feasible) {
      return;
    }
    unbounded = num_eq(0, T[rows][cols - 1]);
    if (unbounded) {
      return;
    }
    value = T[rows][cols] / T[rows][cols - 1];

    solution.resize(V);
    for (int i = 0; i < V; i++) {
      bool found = false;
      for (int j = 0; j < rows; j++) {
        if (num_eq(T[j][i], 0)) {
          continue;
        }
        if (found) {
          solution[i] = 0;
          break;
        }
        solution[i] = T[j][cols] / T[j][i];
        found = true;
      }
    }
  }

  int V;
  int slack;
  vector<vector<num> > A;
  vector<num> B;
  vector<bool> equal;

  /* Output values */
  bool feasible;
  bool unbounded;
  num value;
  vector<num> solution;
};


int main() {
  int N;
  string jnk;

  cin >> N;
  getline(cin, jnk);

  vector<string> names(N);
  for (int i = 0; i < N; i++) {
    getline(cin, names[i]);
  }

  vector<vector<double> > A(N, vector<double>(N, 0.5));
  for (int i = 0; i < N; i++) {
    for (int j = 0; j < N; j++) {
      cin >> A[i][j];
    }
  }

  for (int i = 0; i < N; i++) {
    for (int j = i + 1; j < N; j++) {
      if (!num_eq(A[i][j], 1.0 - A[j][i])) {
        cout << "prob " << i << ", " << j << endl;
      }
    }
  }
  simplex s(N + 1);

  /* sum(v) <= 1 */
  vector<num> ones(N + 1, 1.0);
  ones[N] = 0.0;
  s.halfspace(ones, 1.0);

  for (int i = 0; i < N; i++) {
    /* s <= dot(v, A[i]) */
    vector<num> a;
    for (int j = 0; j < N; j++) {
      a.push_back(-A[j][i]);
    }
    a.push_back(1.0);
    s.halfspace(a, 0.0);
  }

  vector<num> obj(N + 1, 0.0);
  obj[N] = 1;

  s.solve(obj);
  cout << "Value: " << s.value << ", " << s.solution[N] << endl;

  for (int i = 0; i < N; i++) {
    if (num_lt(0.0, s.solution[i])) {
      printf("%s: %.2f%\n", names[i].c_str(), (double)(100 * s.solution[i]));
    }
  }
  for (int i = 0; i <= N; i++) {
    cout << s.solution[i] << endl;
  }

  cout << "Fixed strats:" << endl;
  for (int i = 0; i < N; i++) {
    double p = 0;
    for (int j = 0; j < N; j++) {
      p += A[i][j] * s.solution[j];
    }
    cout << p << endl;
  }

  return 0;
}
