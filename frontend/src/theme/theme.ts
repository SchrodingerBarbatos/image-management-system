import type { ThemeConfig } from 'antd';
import { theme } from 'antd';

export type ThemeMode = 'light' | 'dark';

const common: Pick<ThemeConfig, 'token'> = {
  token: {
    fontFamily:
      '-apple-system, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif',
    fontSize: 13,
    borderRadius: 8,

    controlHeight: 32,
    controlHeightSM: 26,
  },
};

const darkTheme: ThemeConfig = {
  ...common,
  algorithm: theme.darkAlgorithm,
  token: {
    ...common.token,
    colorPrimary: '#7fb3ff',
    colorInfo: '#7fb3ff',
    colorSuccess: '#4ed48a',
    colorWarning: '#f0b454',
    colorError: '#f2665e',
    colorBgBase: '#0e1012',
    colorBgLayout: '#0e1012',
    colorBgContainer: '#181c20',
    colorBgElevated: '#1e2329',
    colorBgSpotlight: '#252b32',
    colorBorder: '#262d34',
    colorBorderSecondary: '#20262c',
    colorText: '#e9ecef',
    colorTextSecondary: '#98a2ac',
    colorTextTertiary: '#5f6a74',
    colorTextQuaternary: '#4a545e',
    colorFillSecondary: 'rgba(255,255,255,0.05)',
    colorFillTertiary: 'rgba(255,255,255,0.03)',
  },
  components: {
    Table: {
      headerBg: '#14171a',
      headerColor: '#98a2ac',
      rowHoverBg: '#1e2329',
      rowSelectedBg: '#1b2530',
      rowSelectedHoverBg: '#1f2a38',
      borderColor: '#20262c',
      cellPaddingBlock: 9,
      cellPaddingInline: 12,
    },
    Modal: {
      contentBg: '#1e2329',
      headerBg: '#1e2329',
      titleColor: '#e9ecef',
    },
    Tabs: {
      itemColor: '#5f6a74',
      itemHoverColor: '#e9ecef',
      itemSelectedColor: '#a9cdff',
      inkBarColor: '#7fb3ff',
    },
    Progress: {
      remainingColor: '#252b32',
      defaultColor: '#7fb3ff',
    },
    Steps: {
      colorTextDescription: '#5f6a74',
    },
    Tag: {
      defaultBg: '#252b32',
      defaultColor: '#98a2ac',
    },
    Select: {
      optionSelectedBg: '#1b2530',
      optionActiveBg: '#1e2329',
    },
    Checkbox: {
      colorPrimary: '#7fb3ff',
      colorPrimaryHover: '#a9cdff',
    },
    Radio: {
      buttonBg: '#181c20',
    },
    Switch: {
      colorPrimary: '#7fb3ff',
      colorPrimaryHover: '#a9cdff',
    },
    Message: {
      contentBg: '#1e2329',
    },
    Tooltip: {
      colorBgSpotlight: '#252b32',
    },
    Popover: {
      colorBgElevated: '#1e2329',
    },
    Empty: {
      colorTextTertiary: '#5f6a74',
    },
    Upload: {
      colorFillAlter: '#181c20',
    },
  },
};

const lightTheme: ThemeConfig = {
  ...common,
  algorithm: theme.defaultAlgorithm,
  token: {
    ...common.token,
    colorPrimary: '#2869c7',
    colorInfo: '#2869c7',
    colorSuccess: '#21834f',
    colorWarning: '#a86408',
    colorError: '#c43f38',
    colorBgBase: '#f4f6f8',
    colorBgLayout: '#f4f6f8',
    colorBgContainer: '#ffffff',
    colorBgElevated: '#ffffff',
    colorBgSpotlight: '#293340',
    colorBorder: '#d8dee6',
    colorBorderSecondary: '#e7ebf0',
    colorText: '#18202a',
    colorTextSecondary: '#596675',
    colorTextTertiary: '#7d8996',
    colorTextQuaternary: '#9aa4ae',
    colorFillSecondary: 'rgba(24,32,42,0.06)',
    colorFillTertiary: 'rgba(24,32,42,0.035)',
  },
  components: {
    Table: {
      headerBg: '#f6f8fa',
      headerColor: '#657181',
      rowHoverBg: '#f4f7fb',
      rowSelectedBg: '#eaf2fd',
      rowSelectedHoverBg: '#e3eefc',
      borderColor: '#e7ebf0',
      cellPaddingBlock: 9,
      cellPaddingInline: 12,
    },
    Modal: {
      contentBg: '#ffffff',
      headerBg: '#ffffff',
      titleColor: '#18202a',
    },
    Tabs: {
      itemColor: '#7d8996',
      itemHoverColor: '#18202a',
      itemSelectedColor: '#215fae',
      inkBarColor: '#2869c7',
    },
    Progress: {
      remainingColor: '#e8edf2',
      defaultColor: '#2869c7',
    },
    Steps: { colorTextDescription: '#7d8996' },
    Tag: { defaultBg: '#eef1f4', defaultColor: '#596675' },
    Select: { optionSelectedBg: '#eaf2fd', optionActiveBg: '#f4f7fb' },
    Checkbox: { colorPrimary: '#2869c7', colorPrimaryHover: '#215fae' },
    Radio: { buttonBg: '#ffffff' },
    Switch: { colorPrimary: '#2869c7', colorPrimaryHover: '#215fae' },
    Message: { contentBg: '#ffffff' },
    Tooltip: { colorBgSpotlight: '#293340' },
    Popover: { colorBgElevated: '#ffffff' },
    Empty: { colorTextTertiary: '#7d8996' },
    Upload: { colorFillAlter: '#f6f8fa' },
  },
};

export const getAppTheme = (mode: ThemeMode): ThemeConfig =>
  mode === 'dark' ? darkTheme : lightTheme;
