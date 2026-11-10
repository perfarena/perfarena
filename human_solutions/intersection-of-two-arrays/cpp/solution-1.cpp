// Initialize an empty set C
set<int> C;

// Iterate over elements of num1 and num2
for (int x : num1) {
    for (int y : num2) {
        if (x == y) {
            C.insert(x);
            break;
        }
    }
}

// Convert set C to a vector result
vector<int> result(C.begin(), C.end());