import { readFileSync } from 'node:fs'
import { networkInterfaces } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { NextConfig } from 'next'

const projectRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

function parseChangelog(content: string) {
    return content
        .split(/^## /m)
        .slice(1)
        .map((block) => {
            const [title = '', ...lines] = block.trim().split('\n')
            const [, version = title.trim(), date = ''] = title.match(/^(.+?)(?:\s+-\s+(.+))?$/) || []
            return {
                version: version.trim(),
                date: date.trim(),
                items: lines
                    .map((line) => line.trim().match(/^\+\s+\[(.+?)\]\s+(.+)$/))
                    .filter((match): match is RegExpMatchArray => Boolean(match))
                    .map((match) => ({ type: match[1], content: match[2] })),
            }
        })
        .filter((release) => release.items.length)
}

function readAppVersion() {
    try {
        const version = readFileSync(join(projectRoot, 'VERSION'), 'utf-8').trim()
        return version || '0.0.0'
    } catch {
        return '0.0.0'
    }
}

function readLocalDevOrigins() {
    return Object.values(networkInterfaces())
        .flatMap((items) => items || [])
        .filter((item) => item.family === 'IPv4' && !item.internal)
        .map((item) => item.address)
}

const appVersion = process.env.NEXT_PUBLIC_APP_VERSION || readAppVersion()
let appReleases = '[]'
try {
    appReleases = JSON.stringify(parseChangelog(readFileSync(join(projectRoot, 'CHANGELOG.md'), 'utf-8')))
} catch {}

const nextConfig: NextConfig = {
    allowedDevOrigins: [...new Set(['127.0.0.1', 'localhost', '192.168.9.71', ...readLocalDevOrigins()])],
    env: {
        NEXT_PUBLIC_APP_VERSION: appVersion,
        NEXT_PUBLIC_APP_RELEASES: appReleases,
    },
    output: 'export',
    trailingSlash: true,
    images: {
        unoptimized: true,
    },
    typescript: {
        ignoreBuildErrors: true,
    },
}

export default nextConfig
