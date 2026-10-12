#include <iostream>
#include <vector>

long long factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}

long long nCr(int n, int r) {
    return factorial(n) / (factorial(r) * factorial(n - r));
}

std::vector<long long> getRow(int rowIndex) {
    std::vector<long long> row(rowIndex + 1, 1);

    for (int i = 1; i < rowIndex; i++) {
        row[i] = nCr(rowIndex, i);
    }

    return row;
}

int main() {
    int rowIndex = 7; // Example: Print the 8th row (0-indexed)
    std::vector<long long> row = getRow(rowIndex);

    for (int i = 0; i <= rowIndex; i++) {
        std::cout << row[i] << " ";
    }

    return 0;
}