#include <stdio.h>

static int pick(int a, int b)
{
    if (a > 1 && b > 1) {
        return 1;
    }
    for (int i = 0; i < a; i++) {
        switch (i) {
        case 1: return 2;
        case 2: return 3;
        default: break;
        }
    }
    return a ? b : 0;
}

int main(void)
{
    printf("%d\n", pick(1, 2));
    return 0;
}
