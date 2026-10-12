#include <iostream>
#include <vector>
using namespace std;

char findSmallestCharacter(vector<char>& letters, char target) {
    for (char ch : letters) {
        if (ch > target) {
            return ch;
        }
    }
    return letters[0];
}

int main() {
    vector<char> letters = {'C', 'F', 'J'};
    char target = 'A';
    char result = findSmallestCharacter(letters, target);
    cout << result << endl;
    return 0;
}