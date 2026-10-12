#include <iostream>
#include <vector>
#include <algorithm>

bool customSort(std::vector<int>& a, std::vector<int>& b) {
    if (a[0] == b[0]) {
        return a[1] < b[1];
    }
    return a[0] > b[0];
}

int countWeakCharacters(std::vector<std::vector<int>>& properties) {
    std::sort(properties.begin(), properties.end(), customSort);
    
    int answer = 0;
    int temp = INT_MIN;
    
    for (auto& p : properties) {
        if (temp < p[1]) {
            temp = p[1];
        } else {
            answer++;
        }
    }
    
    return answer;
}

int main() {
    std::vector<std::vector<int>> properties = {{10, 4}, {4, 3}, {1, 5}};
    int weakCount = countWeakCharacters(properties);
    
    std::cout << "Number of weak characters: " << weakCount << std::endl;
    
    return 0;
}